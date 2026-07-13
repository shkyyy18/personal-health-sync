from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .core import generate_demo, import_csv, load, merge, render_dashboard, save


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="healthsync", description="Local-first health data aggregation.")
    parser.add_argument("--version", action="version", version=f"healthsync {__version__}")
    parser.add_argument("--store", type=Path, default=Path("data/observations.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Generate synthetic data and a portable dashboard")
    demo.add_argument("--days", type=int, default=30)
    demo.add_argument("--dashboard", type=Path, default=Path("dist/dashboard.html"))
    ingest = sub.add_parser("import-csv", help="Import date,code,value CSV records")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source", default="csv")
    status = sub.add_parser("status", help="Show local store summary")
    status.add_argument("--json", action="store_true")
    dashboard = sub.add_parser("dashboard", help="Render the current local store")
    dashboard.add_argument("--output", type=Path, default=Path("dist/dashboard.html"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        items = save(args.store, generate_demo(max(1, args.days)))
        render_dashboard(items, args.dashboard)
        print(f"Generated {len(items)} synthetic observations and {args.dashboard}")
        return 0
    if args.command == "import-csv":
        incoming = import_csv(args.path, args.source)
        items = merge(args.store, incoming)
        print(f"Imported {len(incoming)} observations; store now contains {len(items)}")
        return 0
    items = load(args.store)
    if args.command == "dashboard":
        render_dashboard(items, args.output)
        print(args.output)
        return 0
    summary = {"store": str(args.store), "observations": len(items), "metrics": len({i['code'] for i in items}), "sources": sorted({i['source'] for i in items})}
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else f"{summary['observations']} observations | {summary['metrics']} metrics | {', '.join(summary['sources']) or 'no sources'}")
    return 0
