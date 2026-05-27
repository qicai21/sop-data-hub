#!/usr/bin/env python3
"""Generate dispatch_board_data.json from business & 95306 databases.

Usage:
    python scripts/generate_dispatch_board_data.py \\
        --db data/sop_agent.db \\
        --rail-db /path/to/95306_collection.sqlite3 \\
        --out dashboard/dispatch_board_data.json \\
        --reason manual_refresh

Or use the CLI subcommand:

    python -m ops_hub dispatch-board --reason manual_refresh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running directly from the scripts/ directory
_parent = Path(__file__).resolve().parents[1] / "src"
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate dispatch board JSON data from business & 95306 databases."
    )
    parser.add_argument("--db", required=True, help="Path to sop_agent.db (business database)")
    parser.add_argument(
        "--rail-db",
        required=True,
        help="Path to 95306_collection.sqlite3 (read-only)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output path for dispatch_board_data.json",
    )
    parser.add_argument(
        "--reason",
        default="manual_refresh",
        help="Refresh reason (e.g. release_batch_updated, formal_commit)",
    )
    args = parser.parse_args()

    from ops_hub.data_agent.dispatch_board import generate_dispatch_board_data

    db_path = Path(args.db)
    rail_path = Path(args.rail_db)
    out_path = Path(args.out)

    if not db_path.exists():
        print(f"ERROR: Business database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    if not rail_path.exists():
        print(f"WARNING: 95306 database not found: {rail_path} — continuing without rail data", file=sys.stderr)

    try:
        data = generate_dispatch_board_data(
            business_db_path=db_path,
            rail_db_path=rail_path if rail_path.exists() else None,
            refresh_reason=args.reason,
        )
    except Exception as exc:
        print(f"ERROR: Failed to generate dispatch board data: {exc}", file=sys.stderr)
        sys.exit(1)

    # Atomic write
    tmp_path = out_path.with_suffix(".json.tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(out_path)

    summary = data.get("summary", {})
    print(f"✅ Dispatch board JSON written to: {out_path}")
    print(f"   release_batches: {summary.get('release_batch_total', 0)}")
    print(f"   active: {summary.get('active_release_batches', 0)}")
    print(f"   pending_total: {summary.get('pending_total', 0)}")
    print(f"   formal_wagon_count: {summary.get('formal_wagon_count', 0)}")
    print(f"   formal_weight: {summary.get('formal_weight', 0)}")


if __name__ == "__main__":
    main()
