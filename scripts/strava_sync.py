#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strava数据同步 - Python版
从Strava API拉取最近活动数据，保存到本地
基于现有 strava_sync.ps1 转写
"""

import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from time import sleep

# Windows 控制台可能是 GBK(cp936), 强制 UTF-8 输出, 避免特殊字符/中文崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_URL = "https://www.strava.com/api/v3"

BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = Path(__file__).parent / "config" / "strava_config.json"
DATA_DIR = BASE_DIR / "data" / "strava"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROCESSED_FILE = DATA_DIR / "activities_processed.json"
RAW_FILE = DATA_DIR / "activities_raw.json"


def load_config() -> dict:
    """加载Strava配置"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Strava配置不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def get_env_value(name: str) -> str:
    """读取环境变量: 优先当前进程(os.environ), 回落用户级注册表 HKCU\\Environment。"""
    val = os.environ.get(name, "")
    if val:
        return val
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v)
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def set_env_value(name: str, value: str) -> None:
    """写入用户级环境变量(注册表 HKCU\\Environment), 供下次运行读取。
    用 winreg 直写, 确定性可靠。当前进程的 os.environ 需由调用方同步更新。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    except Exception as e:
        print(f"警告: 写回环境变量 {name} 失败: {e}")


def refresh_access_token(config: dict) -> str:
    """用 refresh_token 换新的 access_token。token 全程走环境变量, 不落盘文件。
    Strava 可能轮换 refresh_token, 故刷新后把新值写回用户级环境变量。"""
    client_secret = get_env_value("STRAVA_CLIENT_SECRET")
    refresh_token = get_env_value("STRAVA_REFRESH_TOKEN")
    if not client_secret:
        print("警告: STRAVA_CLIENT_SECRET 未设置, 无法刷新 token")
        print("请设置: [Environment]::SetEnvironmentVariable('STRAVA_CLIENT_SECRET','你的secret','User')")
        return ""
    if not refresh_token:
        print("警告: STRAVA_REFRESH_TOKEN 未设置, 无法刷新 token")
        print("请把 OAuth 换到的 refresh_token 设进用户级环境变量 STRAVA_REFRESH_TOKEN")
        return ""

    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": config["client_id"],
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    })

    if resp.status_code != 200:
        print(f"Token刷新失败: {resp.status_code} {resp.text[:100]}")
        return ""

    data = resp.json()
    # 写回用户级环境变量(供下次运行); 同步更新当前进程内存
    set_env_value("STRAVA_ACCESS_TOKEN", data["access_token"])
    set_env_value("STRAVA_REFRESH_TOKEN", data["refresh_token"])
    os.environ["STRAVA_ACCESS_TOKEN"] = data["access_token"]
    os.environ["STRAVA_REFRESH_TOKEN"] = data["refresh_token"]
    expires_at = data.get("expires_at", 0)
    exp_str = datetime.fromtimestamp(expires_at) if expires_at else "未知"
    print(f"Token已刷新并写回环境变量, 有效期至: {exp_str}")
    return data["access_token"]


def fetch_activities(access_token: str, days: int = 7) -> list[dict]:
    """从Strava API拉取活动数据"""
    after_ts = int((datetime.now() - timedelta(days=days)).timestamp())
    all_activities = []
    page = 1

    while True:
        url = f"{STRAVA_API_URL}/athlete/activities"
        params = {"after": after_ts, "per_page": 200, "page": page}
        headers = {"Authorization": f"Bearer {access_token}"}

        resp = None
        for attempt in range(1, 4):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=60)
                break
            except requests.exceptions.RequestException as e:
                print(f"  网络错误(第{attempt}/3次): {e}")
                resp = None
                if attempt < 3:
                    sleep(2)
        if resp is None:
            print("连续 3 次请求失败, strava.com 当前连不上(国内访问偶发, 可稍后重试或挂代理)。")
            break

        if resp.status_code == 401:
            print("Token已过期，需要刷新")
            return None  # 信号：需要刷新token

        if resp.status_code != 200:
            print(f"API错误: {resp.status_code}")
            break

        data = resp.json()
        if not data:
            break

        all_activities.extend(data)
        print(f"  第{page}页: 获取{len(data)}条活动 (总计: {len(all_activities)})")

        if len(data) < 200:
            break
        page += 1
        sleep(0.5)

    return all_activities


def process_activities(raw: list[dict]) -> list[dict]:
    """处理原始活动数据"""
    processed = []
    for act in raw:
        processed.append({
            "id": act.get("id"),
            "date": act.get("start_date_local", "")[:10],
            "type": act.get("type", "Unknown"),
            "name": act.get("name", ""),
            "distance_m": round(act.get("distance", 0), 1),
            "duration_s": act.get("moving_time", 0),
            "elevation_m": round(act.get("total_elevation_gain", 0), 1),
            "avg_speed_ms": round(act.get("average_speed", 0), 2),
            "max_speed_ms": round(act.get("max_speed", 0), 2),
            "avg_hr": act.get("average_heartrate"),
            "max_hr": act.get("max_heartrate"),
            "calories": act.get("calories"),
        })
    return processed


def save_activities(raw: list[dict], processed: list[dict]):
    """保存活动数据"""
    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: {len(processed)}条活动")


def sync(days: int = 7) -> bool:
    """主同步函数"""
    config = load_config()  # 仅含 client_id / athlete 等非敏感字段
    access_token = get_env_value("STRAVA_ACCESS_TOKEN")

    # 尝试拉取
    activities = fetch_activities(access_token, days)

    # 如果token过期，刷新后重试
    if activities is None:
        access_token = refresh_access_token(config)
        if not access_token:
            return False
        activities = fetch_activities(access_token, days)

    if not activities:
        print("无新活动数据")
        return True

    processed = process_activities(activities)
    save_activities(activities, processed)
    return True


def get_recent_activities(days: int = 7) -> list[dict]:
    """获取最近N天的活动数据（先尝试在线同步，失败则读本地）"""
    try:
        sync(days)
    except Exception as e:
        print(f"在线同步失败: {e}")

    # 读取本地数据
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, "r", encoding="utf-8-sig") as f:
            all_data = json.load(f)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [a for a in all_data if a.get("date", "") >= cutoff]
    return []


if __name__ == "__main__":
    days = 7
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass
    print(f"同步最近 {days} 天的Strava活动...")
    success = sync(days)
    print("完成!" if success else "同步失败")
