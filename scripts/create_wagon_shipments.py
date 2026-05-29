#!/usr/bin/env python3
"""CLI for create_wagon_shipments — R45.

Usage:
  python scripts/create_wagon_shipments.py 
    --release-batch-id <ID>
    --departure-text '煤六 四平铁 蓝鳍 18节'
    --reference-time "2026-05-23 01:25:00"
    --origin 高桥镇 --destination 四平
    --dry-run

  python scripts/create_wagon_shipments.py 
    --release-batch-id <ID>
    --departure-text '煤六 四平铁 长航滨海 46节'
    --reference-time "2026-05-23 01:25:00"
    --origin 高桥镇 --destination 四平
    --apply
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

from ops_hub.sop.create_wagon_shipments import create_wagon_shipments_from_candidates
from ops_hub.sop.departure_text_parser import parse_departure_text
from ops_hub.sop.query_95306_shipments import query_95306_shipments_by_window


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create wagon_shipments from 95306 candidates (R45)"
    )
    parser.add_argument("--release-batch-id", required=True)
    parser.add_argument("--departure-text", required=True)
    parser.add_argument("--reference-time", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--cargo-name", default="")
    parser.add_argument("--window-before", type=int, default=60)
    parser.add_argument("--window-after", type=int, default=60)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false", default=True)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--rail-db", default=None)

    args = parser.parse_args()

    # ── 1. Parse departure text ────────────────────────────────────
    departure = parse_departure_text(args.departure_text)
    if departure.status == "no_match":
        print(json.dumps({"error": "departure text not matched"}, ensure_ascii=False))
        sys.exit(1)

    # ── 2. Query 95306 ─────────────────────────────────────────────
    query_result = query_95306_shipments_by_window(
        origin_station=args.origin,
        destination_station=args.destination,
        reference_time=args.reference_time,
        window_before_minutes=args.window_before,
        window_after_minutes=args.window_after,
        cargo_name=args.cargo_name,
        expected_car_count=departure.car_count,
        rail_db_path=args.rail_db,
    )

    # ── 3. Create wagon_shipments ──────────────────────────────────
    result = create_wagon_shipments_from_candidates(
        release_batch_id=args.release_batch_id,
        departure_candidate=departure,
        shipment_query_result=query_result,
        dry_run=args.dry_run,
        allow_partial=args.allow_partial,
        allow_existing_skip=args.skip_existing,
        db_path=args.db_path,
        rail_db_path=args.rail_db,
    )

    output = result.to_dict()
    output["departure_candidate"] = departure.to_dict()
    output["query_summary"] = {
        "total_candidates": query_result.total_candidates,
        "window": query_result.window.to_dict(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
