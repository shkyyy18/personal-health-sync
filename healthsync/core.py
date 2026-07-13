from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

REQUIRED_COLUMNS = ("date", "code", "value")


def observation(date_value: str, code: str, value: Any, unit: str = "", source: str = "manual", display: str = "") -> dict[str, Any]:
    day = str(date_value).strip()[:10]
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError(f"Invalid date: {date_value!r}") from exc
    normalized_code = str(code).strip()
    normalized_source = str(source or "manual").strip()
    if not normalized_code:
        raise ValueError("Observation code cannot be empty.")
    if not normalized_source:
        raise ValueError("Observation source cannot be empty.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for {code}: {value!r}") from exc
    return {
        "resourceType": "Observation", "date": day, "code": normalized_code,
        "display": str(display or normalized_code).strip(), "value": numeric, "unit": str(unit).strip(),
        "source": normalized_source,
    }


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Observation store must contain a JSON list.")
    return sorted(data, key=lambda item: (item.get("date", ""), item.get("code", "")))


def save(path: Path, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = sorted(items, key=lambda item: (item["date"], item["code"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def merge(path: Path, new_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(item["date"], item["code"], item.get("source", "")): item for item in load(path)}
    for item in new_items:
        by_key[(item["date"], item["code"], item.get("source", ""))] = item
    return save(path, by_key.values())


def import_csv(csv_path: Path, source: str = "csv") -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(missing)}")
        return [observation(row["date"], row["code"], row["value"], row.get("unit", ""), row.get("source") or source, row.get("display", "")) for row in reader if any(str(value or "").strip() for value in row.values())]


def latest(items: Iterable[dict[str, Any]], code: str) -> dict[str, Any] | None:
    matches = [item for item in items if item.get("code") == code]
    return max(matches, key=lambda item: item["date"]) if matches else None


def daily_summary(items: Iterable[dict[str, Any]], day: str) -> dict[str, dict[str, Any]]:
    return {item["code"]: item for item in items if item.get("date") == day}


def generate_demo(days: int = 30) -> list[dict[str, Any]]:
    end = date.today()
    result: list[dict[str, Any]] = []
    for offset in range(days):
        day = end - timedelta(days=days - offset - 1)
        weight = 72.8 - offset * 0.025 + ((offset % 5) - 2) * 0.04
        result.extend([
            observation(day.isoformat(), "body-weight", round(weight, 2), "kg", "synthetic-demo", "Body weight"),
            observation(day.isoformat(), "steps", 6200 + (offset * 733) % 6200, "steps", "synthetic-demo", "Steps"),
            observation(day.isoformat(), "sleep-duration", round(6.4 + (offset % 6) * 0.22, 2), "hours", "synthetic-demo", "Sleep"),
            observation(day.isoformat(), "resting-heart-rate", 62 - (offset // 10), "bpm", "synthetic-demo", "Resting heart rate"),
        ])
        if offset % 3 == 0:
            result.append(observation(day.isoformat(), "run-distance", 3500 + offset * 80, "m", "synthetic-demo", "Run distance"))
    return result


def render_dashboard(items: list[dict[str, Any]], output: Path) -> Path:
    codes = Counter(item["code"] for item in items)
    latest_rows = []
    for code in sorted(codes):
        item = latest(items, code)
        if item:
            latest_rows.append(f"<tr><td>{html.escape(item['display'])}</td><td><strong>{item['value']:g}</strong> {html.escape(item['unit'])}</td><td>{html.escape(item['date'])}</td><td>{html.escape(item['source'])}</td></tr>")
    payload = (
        json.dumps(items, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal Health Sync</title><style>body{{font:16px system-ui;margin:0;background:#f4f7fb;color:#152033}}main{{max-width:960px;margin:auto;padding:32px}}.hero{{background:linear-gradient(135deg,#0f766e,#2563eb);color:white;padding:32px;border-radius:20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:20px 0}}.card,table{{background:white;border-radius:14px;box-shadow:0 8px 30px #16324f18}}.card{{padding:18px}}table{{width:100%;border-collapse:collapse;overflow:hidden}}th,td{{padding:13px;text-align:left;border-bottom:1px solid #e7edf5}}small{{opacity:.78}}</style>
<main><section class="hero"><h1>Personal Health Sync</h1><p>Local-first, vendor-neutral health observations.</p><small>Generated from {len(items)} records. Synthetic demo data only.</small></section>
<section class="grid"><div class="card"><b>{len(items)}</b><br>observations</div><div class="card"><b>{len(codes)}</b><br>metrics</div><div class="card"><b>{len(set(i['source'] for i in items))}</b><br>sources</div></section>
<h2>Latest values</h2><table><thead><tr><th>Metric</th><th>Value</th><th>Date</th><th>Source</th></tr></thead><tbody>{''.join(latest_rows)}</tbody></table>
<p><small>Portable HTML. Your health records are embedded locally and are not uploaded anywhere.</small></p><script type="application/json" id="health-data">{payload}</script></main></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output
