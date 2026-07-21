#!/usr/bin/env python3
"""Sync 95306 status for all (or selected) active ships + optional closeout.

Default is dry-run (safe). Launchd uses --apply --closeout for daily rhythm.

Examples:
  # discover + dry-run report
  PYTHONPATH=src python3 scripts/sync_active_shipment_status.py --dry-run

  # apply + lifecycle closeout
  PYTHONPATH=src python3 scripts/sync_active_shipment_status.py --apply --closeout

  # one project only
  PYTHONPATH=src python3 scripts/sync_active_shipment_status.py --apply --projects zhongtang_special_steel
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sop_hub.sop.active_status_sync import (  # noqa: E402
    DEFAULT_RAIL_DB,
    DEFAULT_REPORT_DIR,
    DEFAULT_SOP_DB,
    run_active_status_sync,
)


def main() -> int:
    p = argparse.ArgumentParser(description="95306 status sync for active release batches")
    p.add_argument("--sop-db", default=str(DEFAULT_SOP_DB))
    p.add_argument("--rail-db", default=str(DEFAULT_RAIL_DB))
    p.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    p.add_argument(
        "--projects",
        default="",
        help="Comma-separated project ids (default: all with active lots)",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="Plan only (default)")
    mode.add_argument("--apply", action="store_true", help="Write status fields to sop_agent.db")
    p.add_argument(
        "--closeout",
        action="store_true",
        help="After apply, run lifecycle_closeout (ignored on dry-run)",
    )
    p.add_argument("--json", action="store_true", help="Print full JSON report to stdout")
    p.add_argument(
        "--jiusan-cycle-tracking",
        action="store_true",
        help="Also refresh the four-probe Jiusan cycle tracking cache",
    )
    args = p.parse_args()

    dry_run = not args.apply
    projects = [x.strip() for x in args.projects.split(",") if x.strip()] or None

    report = run_active_status_sync(
        sop_db=args.sop_db,
        rail_db=args.rail_db,
        dry_run=dry_run,
        run_closeout=bool(args.closeout),
        projects=projects,
        report_dir=args.report_dir,
    )
    d = report.to_dict()
    cycle_tracking = None
    if args.jiusan_cycle_tracking:
        try:
            from sop_hub.sop.jiusan_cycle_tracking import update_cycle_tracking_cache

            cycle_tracking = update_cycle_tracking_cache(sop_db=args.sop_db, rail_db=args.rail_db)
        except Exception as exc:  # keep the ordinary status sync report visible
            cycle_tracking = {"error": str(exc), "error_count": 1, "trains": []}
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(
            f"mode={'dry-run' if dry_run else 'apply'} ships={d['ship_count']} "
            f"units={d['totals']['units']} matched={d['totals']['matched']} "
            f"updates={d['totals']['updates']} errors={d['totals']['errors']}"
        )
        for s in d["ships"]:
            err = f" ERR={s['error']}" if s.get("error") else ""
            print(
                f"  {s['project_id']}/{s['ship_name']}: "
                f"units={s['total_units']} matched={s['matched']} "
                f"updates={s['update_count']} deliv={s['delivered']}{err}"
            )
        if d.get("closeout"):
            print(f"closeout={ {k:v for k,v in d['closeout'].items() if k!='advanced_batches'} }")
        if d.get("report_path"):
            print(f"report={d['report_path']}")
        if cycle_tracking is not None:
            print(
                "jiusan_cycle_tracking="
                f"trains:{len(cycle_tracking.get('trains') or [])} "
                f"queries:{cycle_tracking.get('query_count', 0)} "
                f"errors:{cycle_tracking.get('error_count', 0)}"
            )
            if cycle_tracking.get("error"):
                print(f"jiusan_cycle_tracking_error={cycle_tracking['error']}")
    cycle_errors = int((cycle_tracking or {}).get("error_count") or 0)
    return 1 if d["totals"]["errors"] or cycle_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
