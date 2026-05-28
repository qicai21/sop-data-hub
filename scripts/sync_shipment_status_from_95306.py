#!/usr/bin/env python3
"""Sync 95306 shipment status snapshots into sop_agent.db wagon_shipments.

Usage:
  Dry-run:
    python scripts/sync_shipment_status_from_95306.py \\
      --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --dry-run

  Apply:
    python scripts/sync_shipment_status_from_95306.py \\
      --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --apply

Reads wagon_shipments from sop_agent.db, queries 95306_collection.sqlite3
for latest shipment status, and writes departed_at / arrived_at / delivered_at
back into sop_agent.db (only with --apply).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ops_hub.sop.shipment_status_sync import ShipmentStatusSync

DEFAULT_SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_RAIL_DB = Path.home() / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync 95306 shipment status into sop_agent.db"
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Filter by project_id (e.g. jilin_jingang_jinzhou)",
    )
    parser.add_argument(
        "--ship-name",
        required=True,
        help="Filter by ship_name (e.g. 蓝鳍)",
    )
    parser.add_argument(
        "--sop-db",
        default=str(DEFAULT_SOP_DB),
        help=f"Path to sop_agent.db (default: {DEFAULT_SOP_DB})",
    )
    parser.add_argument(
        "--rail-db",
        default=str(DEFAULT_RAIL_DB),
        help=f"Path to 95306_collection.sqlite3 (default: {DEFAULT_RAIL_DB})",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Plan updates, do not write")
    group.add_argument("--apply", action="store_true", help="Write updates to sop_agent.db")

    args = parser.parse_args()

    dry_run = args.dry_run is True

    sync = ShipmentStatusSync(
        sop_db_path=args.sop_db,
        rail_db_path=args.rail_db,
    )

    if not Path(args.rail_db).exists():
        print(json.dumps({
            "error": "rail_db_not_found",
            "rail_db": args.rail_db,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = sync.sync(
        project_id=args.project_id,
        ship_name=args.ship_name,
        dry_run=dry_run,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

    if result.schema_missing_fields:
        print(f"\n⚠️  Schema missing fields: {result.schema_missing_fields}", file=sys.stderr)

    if result.unmatched_count > 0:
        print(f"\n⚠️  {result.unmatched_count} wagons unmatched: {result.unmatched_wagons}", file=sys.stderr)

    if result.update_count == 0:
        sys.exit(0)

    if dry_run:
        print(f"\n[Dry-run] Would update {result.update_count} fields across {result.matched_count} wagons.", file=sys.stderr)
        print(f"  departed_at:  {result.departed_update_count}", file=sys.stderr)
        print(f"  arrived_at:   {result.arrived_update_count}", file=sys.stderr)
        print(f"  delivered_at: {result.delivered_update_count}", file=sys.stderr)
        print(f"  batch_level_suggestion: {result.batch_level_suggestion}", file=sys.stderr)
    else:
        print(f"\n[Apply] Updated {result.update_count} fields across {result.matched_count} wagons.", file=sys.stderr)
        print(f"  departed_at:  {result.departed_update_count}", file=sys.stderr)
        print(f"  arrived_at:   {result.arrived_update_count}", file=sys.stderr)
        print(f"  delivered_at: {result.delivered_update_count}", file=sys.stderr)
        print(f"  batch_level_suggestion: {result.batch_level_suggestion}", file=sys.stderr)


if __name__ == "__main__":
    main()
