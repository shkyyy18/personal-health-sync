# -*- coding: utf-8 -*-
"""
健康数据 FHIR-lite 统一层 (借鉴 Fasten Health 的标准化思路)
================================================================
现有 health_advisor/data 下是异构 JSON(体成分records/心率按日期键/睡眠records/
压力按日期键/血氧/每日活动/Strava list)。本模块把它们归一成统一的 FHIR-lite
Observation 资源, 提供单一查询入口, 让 health-advisor agent 和未来 dashboard 拿到
结构化上下文, 不再各自解析散落文件。

FHIR-lite Observation = {
  resourceType: 'Observation',
  code:         'body-weight' | 'heart-rate' | 'steps' | ... (LOINC-ish 短码),
  display:      中文/英文显示名,
  date:         'YYYY-MM-DD',
  value:        数值,
  unit:         'kg' | 'bpm' | 'steps' | 'hours' | ...,
  source:       'body_composition' | 'heart_rate' | 'strava' | ...
}

注: 这是 Fasten "多源→统一标准化层" 思路的本地轻量实现, 不引入 Fasten 全套
(Go+Angular+病历连接器对中国无价值)。只取其数据模型精华。
"""
import json
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
OBS_FILE = DATA_DIR / 'fhir_observations.json'


def _load(name):
    p = DATA_DIR / f'{name}.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _obs(code, display, date, value, unit, source):
    if value is None or date is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return {'resourceType': 'Observation', 'code': code, 'display': display,
            'date': str(date)[:10], 'value': value, 'unit': unit, 'source': source}


# ── 各源归一化 ─────────────────────────────────────────────
def _from_body_composition(out):
    d = _load('body_composition') or {}
    for r in (d.get('records') or []):
        date = r.get('date')
        for k, (code, disp, unit) in {
            'weight_kg': ('body-weight', '体重', 'kg'),
            'bmi': ('bmi', 'BMI', ''),
            'body_fat_pct': ('body-fat-pct', '体脂率', '%'),
            'muscle_kg': ('muscle-mass', '肌肉量', 'kg'),
            'muscle_pct': ('muscle-pct', '肌肉率', '%'),
            'protein_kg': ('protein-mass', '蛋白质量', 'kg'),
            'water_pct': ('water-pct', '水分率', '%'),
        }.items():
            o = _obs(code, disp, date, r.get(k), unit, 'body_composition')
            if o:
                out.append(o)


def _from_daily_keyed(out, fname, items):
    """按日期键的聚合源(心率/压力/血氧/每日活动), items=[(json键, code, display, unit)]"""
    d = _load(fname)
    if not isinstance(d, dict):
        return
    for date, v in d.items():
        if not isinstance(v, dict):
            continue
        for key, code, disp, unit in items:
            o = _obs(code, disp, date, v.get(key), unit, fname)
            if o:
                out.append(o)


def _from_sleep(out):
    d = _load('sleep_records') or {}
    for r in (d.get('records') or []):
        date = r.get('date')
        for k, (code, disp) in {
            'duration_hours': ('sleep-duration', '睡眠时长'),
            'deep_sleep_hours': ('deep-sleep', '深睡'),
            'light_sleep_hours': ('light-sleep', '浅睡'),
            'rem_sleep_hours': ('rem-sleep', 'REM'),
        }.items():
            o = _obs(code, disp, date, r.get(k), 'hours', 'sleep_records')
            if o:
                out.append(o)


def _from_strava(out):
    d = _load('strava/activities_processed')
    if not isinstance(d, list):
        return
    for a in d:
        date = a.get('date')
        for k, (code, disp, unit) in {
            'distance_m': ('run-distance', '运动距离', 'm'),
            'duration_s': ('run-duration', '运动时长', 's'),
            'elevation_m': ('run-elevation', '爬升', 'm'),
            'avg_speed_ms': ('run-speed', '平均配速', 'm/s'),
        }.items():
            o = _obs(code, disp, date, a.get(k), unit, 'strava')
            if o:
                out.append(o)


# ── 主入口 ─────────────────────────────────────────────────
def build_all():
    """读全部健康源 → 统一 Observation 列表(按 date 排序)。"""
    out = []
    _from_body_composition(out)
    _from_daily_keyed(out, 'heart_rate', [
        ('avg', 'heart-rate', '平均心率', 'bpm'),
        ('min', 'heart-rate-min', '最低心率', 'bpm'),
        ('max', 'heart-rate-max', '最高心率', 'bpm'),
    ])
    _from_daily_keyed(out, 'daily_activity', [
        ('steps', 'steps', '步数', 'steps'),
        ('calories', 'calories', '消耗', 'kcal'),
        ('active_minutes', 'active-minutes', '活动时长', 'min'),
        ('distance_m', 'walk-distance', '步行距离', 'm'),
    ])
    _from_daily_keyed(out, 'stress', [('avg', 'stress', '压力', '')])
    _from_daily_keyed(out, 'spo2', [('avg', 'spo2', '血氧', '%')])
    _from_sleep(out)
    _from_strava(out)
    out.sort(key=lambda o: (o['date'], o['code']))
    return out


def save():
    """构建并落盘统一 FHIR-lite 文件, 供 dashboard/agent 读取。"""
    obs = build_all()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OBS_FILE.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding='utf-8')
    return obs


# ── 查询 API ───────────────────────────────────────────────
def _obs_index():
    return save() if not OBS_FILE.exists() else json.loads(OBS_FILE.read_text(encoding='utf-8'))


def latest(code):
    """某指标最新一条。"""
    obs = [o for o in _obs_index() if o['code'] == code]
    return obs[-1] if obs else None


def history(code, days=30):
    """某指标最近 days 天序列。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    return [o for o in _obs_index() if o['code'] == code and o['date'] >= cutoff]


def codes():
    """所有可用指标码 + 计数。"""
    from collections import Counter
    return dict(Counter(o['code'] for o in _obs_index()))


def daily_summary(date=None):
    """某天全部指标(默认今天)。"""
    date = date or datetime.now().strftime('%Y-%m-%d')
    return {o['code']: o for o in _obs_index() if o['date'] == date}


if __name__ == '__main__':
    obs = save()
    print(f'统一 Observation: {len(obs)} 条')
    print('可用指标码:', codes())
    w = latest('body-weight')
    print(f'最新体重: {w["value"]}{w["unit"]} @ {w["date"]}' if w else '无体重数据')
    print(f'近30天心率记录: {len(history("heart-rate", 30))} 天')
