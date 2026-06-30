#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
米家健康数据自动同步 (体脂秤 S800 + 手环, 经米家云)
================================================================
对标 strava_sync.py 的自动化模式:
  - 一次扫码登录米家APP → 认证持久化到 data/.mijia/auth.json
  - 之后 cron 定时自动拉, 零手动 (像 Strava 配一次 token 后全自动)
  - 体脂数据 merge 入 body_composition.json → health_fhir.py 自动接管
  - 彻底替代体脂秤截图 OCR 流程 (extract_latest.py)

⚠ auth.json 含米家登录态, 切勿外传/上传。

用法:
  python mijia_health_sync.py discover   # 首次: 出二维码扫码, 打印设备+属性(贴回给Claude定映射)
  python mijia_health_sync.py sync       # 日常: 自动拉体脂, merge, 刷新FHIR
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AUTH_DIR = DATA_DIR / ".mijia"
AUTH_DIR.mkdir(parents=True, exist_ok=True)
AUTH_PATH = AUTH_DIR / "auth.json"          # ⚠ 含米家登录态, 勿外传
BODY_FILE = DATA_DIR / "body_composition.json"

SCALE_KEYWORDS = ("scale", "scales")         # S800 = xiaomi.scales.ms116
BAND_KEYWORDS = ("band", "watch", "bracelet", "fitness")


def get_api():
    """登录米家: token 有效则自动刷新免扫码; 失效则生成二维码图片+链接 (米家APP扫)。
    设全局 socket timeout: 库内 requests.get 无 timeout, 否则网络慢会无限卡死 ('没反应')。"""
    import socket
    socket.setdefaulttimeout(30)  # ponytail: 库内请求无 timeout 的全局兜底
    from mijiaAPI import mijiaAPI
    import qrcode
    api = mijiaAPI(str(AUTH_PATH))
    login_data = api._get_qr_login_data()  # 不阻塞, 仅取二维码/检测token
    if login_data.get("refreshed"):
        print("✓ 登录态有效 (token 自动刷新), 免扫码")
        return api
    # 需扫码: 二维码存 PNG + 打印链接, 不依赖终端 ASCII 显示
    qr_path = AUTH_DIR / "login_qr.png"
    try:
        qrcode.make(login_data["loginUrl"]).save(str(qr_path))
        print(f"  (二维码图片已存: {qr_path})")
    except Exception as e:
        print(f"  (二维码图片生成失败: {e})")
    print("\n" + "=" * 56)
    print("  扫码登录米家: 打开「米家」APP → 右上角「+」/扫一扫")
    print("=" * 56)
    print(f"  方式1 双击图片扫码: {qr_path}")
    print(f"  方式2 浏览器打开链接: {login_data.get('qr', login_data['loginUrl'])}")
    print("  扫码确认后脚本自动继续, 勿关窗口...\n")
    api._complete_qr_login(login_data)  # 长轮询等待扫码完成
    print("✓ 扫码登录成功, 认证已保存")
    return api


def _match(model: str, keywords) -> bool:
    m = (model or "").lower()
    return any(k in m for k in keywords)


def _all_devices(api) -> list:
    dev = api.get_devices_list()
    try:
        dev += api.get_shared_devices_list()
    except Exception:
        pass
    return dev


def discover(api):
    """列全部设备 + 对体脂秤/手环盲扫所有属性当前值 + 探测历史统计。
    输出贴回给 Claude, 用于确定 sync 的属性映射。"""
    from mijiaAPI import get_device_info
    devs = _all_devices(api)
    print(f"\n===== 米家设备共 {len(devs)} 个 =====")
    for d in devs:
        print(f"  {d.get('name')} | model={d.get('model')} | did={d.get('did')}")

    targets = [d for d in devs if _match(d.get('model', ''), SCALE_KEYWORDS + BAND_KEYWORDS)]
    if not targets:
        print("\n⚠ 未发现体脂秤/手环类设备。请确认:")
        print("  1) S800 体脂秤已在「米家」APP 绑定 (不是只在小米运动健康)")
        print("  2) 小米音箱、手环是否也在同一米家账号下")
        return

    for d in targets:
        did = d.get('did')
        model = d.get('model', '')
        print(f"\n===== {d.get('name')} ({model}) did={did} =====")
        # 规格 (新设备米家规格平台未必收录, 可能为空)
        try:
            info = get_device_info(model)
            svcs = info.get('services', []) if isinstance(info, dict) else []
            if svcs:
                print("  -- MIoT 规格 --")
                for svc in svcs:
                    siid = svc.get('iid') or svc.get('siid')
                    for p in svc.get('properties', []):
                        print(f"  siid.{siid} piid.{p.get('iid')} {p.get('name', '?')}: "
                              f"{p.get('description', '') or p.get('format', '')}")
            else:
                print("  -- 规格为空 (新设备未收录), 走盲扫 --")
        except Exception as e:
            print(f"  规格获取失败({e}), 走盲扫")

        # 盲扫当前值: 直接读 siid 1..8 / piid 1..12, 不依赖规格
        print("  -- 当前值盲扫 --")
        found = []
        for siid in range(1, 9):
            for piid in range(1, 13):
                try:
                    r = api.get_devices_prop({"did": did, "siid": siid, "piid": piid})
                except Exception:
                    r = None
                if r and r.get('code', 0) == 0 and r.get('value') is not None:
                    found.append((siid, piid, r.get('value')))
                    print(f"    siid.{siid} piid.{piid} = {r.get('value')}  (format={r.get('format', '')})")
        if not found:
            print("    (盲扫无值, 设备可能离线或需先在APP触发一次测量)")

        # 历史统计探测 (体脂秤未必走此接口, 试常见体重位)
        print("  -- 历史统计探测 (stat_day_v3 近7天) --")
        for key in ("2.1", "2.2", "3.1", "1.1"):
            try:
                stats = api.get_statistics({
                    "did": did, "key": key, "data_type": "stat_day_v3", "limit": 7,
                    "time_start": int(time.time() - 7 * 86400), "time_end": int(time.time()),
                })
                if stats:
                    print(f"    key={key}: {stats}")
                    break
            except Exception:
                continue


# 体脂秤 MIoT 属性名 → body_composition 字段映射
# ponytail: 待 discover 跑通后, 用真实属性名填准 (当前为占位, sync 会提示未配)
SCALE_PROP_MAP = {
    # 'weight': 'weight_kg',
    # 'body-fat-rate': 'body_fat_pct',
    # 'bmi': 'bmi',
    # ...
}


def sync_scale(api) -> bool:
    """拉体脂秤最新数据 → merge 入 body_composition.json (按日期去重)。"""
    if not SCALE_PROP_MAP:
        print("⚠ 属性映射未配置: 请先跑 `python mijia_health_sync.py discover`, 把输出贴回给 Claude 填映射。")
        return False

    devs = _all_devices(api)
    scale = next((d for d in devs if _match(d.get('model', ''), SCALE_KEYWORDS)), None)
    if not scale:
        print("未找到体脂秤, 跳过")
        return False

    from mijiaAPI import mijiaDevice
    try:
        dev = mijiaDevice(api, did=scale['did'], sleep_time=1.0)
        got = {}
        for prop, field in SCALE_PROP_MAP.items():
            try:
                got[field] = dev.get(prop)
            except Exception:
                pass
    except Exception as e:
        print(f"体脂秤读取失败: {e}")
        return False

    if not got.get('weight_kg'):
        print("未读到体重值")
        return False

    got['date'] = datetime.now().strftime('%Y-%m-%d')
    data = json.loads(BODY_FILE.read_text(encoding='utf-8-sig')) if BODY_FILE.exists() \
        else {"profile": {}, "records": []}
    dates = {r['date'] for r in data.get('records', [])}
    if got['date'] in dates:
        print(f"今日 {got['date']} 已有记录, 跳过")
        return True
    data.setdefault('records', []).append(got)
    data['records'].sort(key=lambda r: r['date'])
    BODY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"✓ 新增体脂记录 {got['date']}: {got.get('weight_kg')}kg / 体脂 {got.get('body_fat_pct', '?')}%")
    return True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sync"
    api = get_api()
    if mode == "discover":
        discover(api)
    elif mode == "sync":
        changed = sync_scale(api)
        if changed:
            sys.path.insert(0, str(BASE_DIR / "scripts"))
            try:
                import health_fhir
                health_fhir.save()
                print("✓ FHIR 统一层已刷新 (agent/dashboard 立刻可读)")
            except Exception as e:
                print(f"FHIR 刷新失败: {e}")
    else:
        print(f"未知模式: {mode} (用 discover 或 sync)")


if __name__ == "__main__":
    main()
