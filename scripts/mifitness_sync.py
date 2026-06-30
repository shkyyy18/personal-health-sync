#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米运动健康云 → 体脂秤 S800 自动同步 (走 hlth.io.mi.com)
================================================================
- 凭据复用米家扫码登录态 (data/.mijia/auth.json 的 userId+passToken,
  小米账号级凭据跨服务通用), 无需再次登录
- 拉体脂数据 → merge 入 body_composition.json → health_fhir 自动接管
- 彻底替代体脂秤截图 OCR 流程 (extract_latest.py)

直接用 adapter 底层 _fetch_key 拿原始 data_list 自行解析, 绕开 binglua 的
pydantic 模型 (其对 bmi=0 的"只称重"记录校验失败会中断整个 async generator,
导致后续数据丢失)。字段名取小米云原始 key (body_fat_rate/muscle_rate/...)。

依赖: mi-fitness-mcp (health_advisor/.vendor/mi-fitness-mcp-cn, 已 pip install -e)
用法:
  python mifitness_sync.py             # 同步最近7天 (适合 cron)
  python mifitness_sync.py --backfill  # 同步最近90天 (历史回填)
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MIJIA_AUTH = DATA_DIR / ".mijia" / "auth.json"   # 复用米家登录态
BODY_FILE = DATA_DIR / "body_composition.json"


def load_credentials() -> tuple[str, str]:
    """从米家登录态读 userId + passToken (账号级, 可换 miothealth sid 的 serviceToken)。"""
    if not MIJIA_AUTH.exists():
        raise FileNotFoundError(
            f"未找到米家登录态 {MIJIA_AUTH}; 先跑 mijia_health_sync.py discover 扫码登录一次")
    d = json.loads(MIJIA_AUTH.read_text(encoding="utf-8"))
    if "userId" not in d or "passToken" not in d:
        raise KeyError("auth.json 缺 userId/passToken, 重跑米家扫码")
    return str(d["userId"]), d["passToken"]


async def fetch_weight_items(days: int) -> list[dict]:
    """连接 Mi Fitness 云, 拉最近 days 天的 weight 原始 data_list。"""
    from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
    user_id, pass_token = load_credentials()
    adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region="cn")
    if not await adapter.connect():
        raise RuntimeError("Mi Fitness 云连接失败 (passToken 可能过期, 重跑 mijia_health_sync.py discover 刷新)")
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    items = await adapter._fetch_key("weight", start, end)
    await adapter.close()
    return items


def _parse(item: dict) -> tuple[str, datetime, dict]:
    """原始 data_list item → (date_str, measured_dt, payload_dict)。"""
    raw = item.get("value", "{}")
    payload = raw if isinstance(raw, dict) else json.loads(raw)
    ts = int(item.get("time", 0))
    zo = int(item.get("zone_offset", 0) or 0)
    dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(seconds=zo)))
    return dt.date().isoformat(), dt, payload


def _optf(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f == 0 else f
    except (TypeError, ValueError):
        return None


def _opti(v) -> int | None:
    if v is None:
        return None
    try:
        i = int(float(v))
        return None if i == 0 else i
    except (TypeError, ValueError):
        return None


def pick_best_per_day(items: list[dict]) -> dict:
    """一天多条 → 取最有代表性的一条: 优先有体脂率的, 其次时间最新。
    (S800 一天上秤多次, 含只称重无体脂的记录, 需筛选完整测量)"""
    by_date: dict = {}
    for item in items:
        date, dt, payload = _parse(item)
        has_fat = _optf(payload.get("body_fat_rate")) is not None
        cur = by_date.get(date)
        if cur is None:
            by_date[date] = (dt, payload)
            continue
        cur_has = _optf(cur[1].get("body_fat_rate")) is not None
        if has_fat and not cur_has:
            by_date[date] = (dt, payload)
        elif has_fat == cur_has and dt > cur[0]:
            by_date[date] = (dt, payload)
    return by_date


def to_record(date: str, payload: dict) -> dict:
    """小米云 weight payload → body_composition.json 字段。"""
    rec = {
        "date": date,
        "weight_kg": _optf(payload.get("weight")),
        "bmi": _optf(payload.get("bmi")),
        "body_fat_pct": _optf(payload.get("body_fat_rate")),
        "muscle_pct": _optf(payload.get("muscle_rate")),
        "water_pct": _optf(payload.get("moisture_rate")),
        "bone_salt_kg": _optf(payload.get("bone_mass")),
        "visceral_fat": _opti(payload.get("visceral_fat")),
        "basal_metabolism_kcal": _opti(payload.get("basal_metabolism")),
        "body_age": _opti(payload.get("body_age")),
        "source": "mi_fitness_cloud",
    }
    return {k: v for k, v in rec.items() if v is not None}


def merge_body_composition(best: dict) -> tuple[int, int]:
    """按 date 去重 merge: 已有该日记录则保留现有(不破坏手动 OCR 的更全字段), 仅新增缺失日期。"""
    data = json.loads(BODY_FILE.read_text(encoding="utf-8-sig")) if BODY_FILE.exists() \
        else {"profile": {}, "records": []}
    existing = {r["date"] for r in data.get("records", [])}
    added = 0
    for date, (_dt, payload) in best.items():
        if date in existing:
            continue
        data["records"].append(to_record(date, payload))
        added += 1
    data["records"].sort(key=lambda r: r["date"])
    BODY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added, len(data["records"])


def main():
    days = 90 if "--backfill" in sys.argv else 7
    print(f"同步 Mi Fitness 体脂数据 (最近 {days} 天)...")
    items = asyncio.run(fetch_weight_items(days))
    print(f"  云端拉到 {len(items)} 条原始测量记录")
    best = pick_best_per_day(items)
    print(f"  去重后 {len(best)} 天")
    added, total = merge_body_composition(best)
    print(f"  ✓ 新增 {added} 天, body_composition 共 {total} 条")
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    try:
        import health_fhir
        health_fhir.save()
        print("  ✓ FHIR 统一层已刷新 (agent/dashboard 立刻可读)")
    except Exception as e:
        print(f"  FHIR 刷新失败: {e}")


if __name__ == "__main__":
    main()
