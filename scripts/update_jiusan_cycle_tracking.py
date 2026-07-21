#!/usr/bin/env python3
"""Refresh the Jiusan four-probe live tracking cache."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sop_hub.sop.jiusan_cycle_tracking import (  # noqa: E402
    DEFAULT_ACCOUNT,
    DEFAULT_CACHE,
    DEFAULT_RAIL_DB,
    DEFAULT_SOP_DB,
    update_cycle_tracking_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Jiusan cycle-train 95306 tracking probes")
    parser.add_argument("--sop-db", default=str(DEFAULT_SOP_DB))
    parser.add_argument("--rail-db", default=str(DEFAULT_RAIL_DB))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = update_cycle_tracking_cache(
        sop_db=args.sop_db,
        rail_db=args.rail_db,
        cache_path=args.cache,
        account=args.account,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"jiusan-cycle-tracking trains={len(report['trains'])} "
            f"queries={report['query_count']} errors={report['error_count']} "
            f"cache={args.cache}"
        )
        for train in report["trains"]:
            print(
                f"  #{train['cycle_no']} {train['ship_name']} {train['trip_key']} "
                f"{train['sample_count']} probes -> {train['node_label']}"
            )
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
