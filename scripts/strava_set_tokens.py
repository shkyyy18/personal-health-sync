#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Strava OAuth 一次性换 token 并写入用户级环境变量。

用法:
    python scripts/strava_set_tokens.py

交互式提示输入 authorization code 和 client_secret(secret 不回显)。
成功并经注册表回读校验后, 写入用户级环境变量:
    STRAVA_CLIENT_SECRET / STRAVA_ACCESS_TOKEN / STRAVA_REFRESH_TOKEN
带网络重试(strava.com 国内访问偶发超时), 落盘后回读验证, 杜绝假成功。
"""
import getpass
import os
import sys
import winreg
from pathlib import Path

import requests

# Windows 控制台可能是 GBK(cp936), 强制 UTF-8 输出, 避免特殊字符/中文崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 复用 strava_sync 里已验证可用的常量与函数
sys.path.insert(0, str(Path(__file__).parent))
from strava_sync import STRAVA_TOKEN_URL, load_config, set_env_value  # noqa: E402

MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 60


def _reg_read(name: str):
    """直接从注册表读(绕过 os.environ), 用于验证落盘。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v)
    except Exception:
        return None


def main() -> int:
    config = load_config()
    client_id = str(config.get("client_id", ""))
    print("=" * 56)
    print(f"Strava OAuth 换 token  (client_id = {client_id})")
    print("=" * 56)
    print("authorization code: 浏览器授权后, 地址栏 code= 后面那串(一次性, 必须是新的)。")
    code = input("  请输入 code > ").strip()
    secret = getpass.getpass("  请输入 client_secret (输入不回显) > ").strip()

    if not code or not secret:
        print("[FAIL] code 和 client_secret 都不能为空。")
        return 1

    # 交换 token, 带重试(strava.com 国内访问偶发超时)
    data = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n[第{attempt}/{MAX_ATTEMPTS}次] 正在向 Strava 交换 token ...")
        try:
            resp = requests.post(STRAVA_TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": secret,
                "code": code,
                "grant_type": "authorization_code",
            }, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            print(f"  网络错误: {e}")
            if attempt < MAX_ATTEMPTS:
                print("  strava.com 国内访问偶发超时, 将重试 ...")
            continue
        if resp.status_code != 200:
            print(f"\n[FAIL] 交换失败: HTTP {resp.status_code}")
            print(f"  Strava 返回: {resp.text[:200]}")
            if resp.status_code in (400, 401):
                print("  -> 常见原因: client_secret 填错 / code 已被用过(需重新授权拿新 code) / code 过期。")
            return 1
        data = resp.json()
        break

    if not data:
        print(f"\n[FAIL] 连续 {MAX_ATTEMPTS} 次网络失败, strava.com 当前连不上。")
        print("  请稍后重试; 若长期不通, 需挂代理/VPN 访问 strava.com。")
        return 1

    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    if not access_token or not refresh_token:
        print(f"\n[FAIL] 返回里缺少 access_token / refresh_token: {data}")
        return 1

    # 写入用户级环境变量(注册表) + 当前进程内存
    set_env_value("STRAVA_CLIENT_SECRET", secret)
    set_env_value("STRAVA_ACCESS_TOKEN", access_token)
    set_env_value("STRAVA_REFRESH_TOKEN", refresh_token)
    os.environ["STRAVA_CLIENT_SECRET"] = secret
    os.environ["STRAVA_ACCESS_TOKEN"] = access_token
    os.environ["STRAVA_REFRESH_TOKEN"] = refresh_token

    # 关键: 直接从注册表回读, 验证确实落盘(不用 os.environ, 它会优先返回内存值)
    ok = (
        _reg_read("STRAVA_CLIENT_SECRET") == secret
        and _reg_read("STRAVA_ACCESS_TOKEN") == access_token
        and _reg_read("STRAVA_REFRESH_TOKEN") == refresh_token
    )

    athlete = data.get("athlete", {}) or {}
    if ok:
        print("\n" + "=" * 56)
        print(f"[OK] 成功并已验证落盘!  athlete = {athlete.get('firstname', '')} {athlete.get('lastname', '')}"
              f" (id={athlete.get('id', '')})")
        print(f"  STRAVA_ACCESS_TOKEN   len={len(access_token)}  [已写入注册表]")
        print(f"  STRAVA_REFRESH_TOKEN  len={len(refresh_token)}  [已写入注册表]")
        print(f"  STRAVA_CLIENT_SECRET  len={len(secret)}  [已写入注册表]")
        print("=" * 56)
        print("下一步: python scripts/strava_sync.py 7")
        return 0
    else:
        print("\n[FAIL] 写入注册表后回读不一致, 落盘失败。请把上面输出发我排查。")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
