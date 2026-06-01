#!/usr/bin/env python3
"""CLI for querying 95306 shipments by time window — R42.

Usage:
  python scripts/query_95306_shipments.py --origin 锦州港 --destination 四平 \
      --reference-time "2026-05-21 09:00:00"

  python scripts/query_95306_shipments.py --origin 锦州港 --destination 四平 \
      --reference-time "2026-05-21 09:00:00" --window-before 120 --window-after 120

  python scripts/query_95306_shipments.py --origin 锦州港 --destination 四平 \
      --reference-time "2026-05-21 09:00:00" --cargo-name 铁矿
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query 95306 shipments by time window (read-only)"
    )
    parser.add_argument("--origin", required=True, help="发站名称 (e.g. 锦州港, 高桥镇)")
    parser.add_argument("--destination", required=True, help="到站名称 (e.g. 四平, 汐子)")
    parser.add_argument("--reference-time", required=True, help="参考时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--window-before", type=int, default=60, help="向前分钟数 (default: 60)")
    parser.add_argument("--window-after", type=int, default=60, help="向后分钟数 (default: 60)")
    parser.add_argument("--cargo-name", default="", help="货物品名过滤 (optional)")
    parser.add_argument("--expected-car-count", type=int, default=-1, help="预期车数 (optional)")
    parser.add_argument("--project-id", default="", help="项目ID (optional)")
    parser.add_argument("--rail-db", default=None, help="95306_collection.sqlite3 路径")

    args = parser.parse_args()

    result = query_95306_shipments_by_window(
        origin_station=args.origin,
        destination_station=args.destination,
        reference_time=args.reference_time,
        window_before_minutes=args.window_before,
        window_after_minutes=args.window_after,
        cargo_name=args.cargo_name,
        expected_car_count=args.expected_car_count,
        project_id=args.project_id,
        rail_db_path=args.rail_db,
    )

    output = result.to_dict()
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
