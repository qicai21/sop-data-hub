#!/usr/bin/env python3
"""Backfill dispatch_train_code for wagon shipment tables.

Dry-run by default. Use --apply to write `dispatch_train_code` into
`wagon_shipments` and `wagon_container_shipments`.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sop_hub.sop.dispatch_train_code import assign_dispatch_train_codes


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "sop_agent.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to sop_agent.db")
    parser.add_argument("--apply", action="store_true", help="Write changes")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate existing dispatch_train_code values",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            result = assign_dispatch_train_codes(conn, overwrite=args.overwrite)
            conn.commit()
        else:
            conn.execute("BEGIN")
            result = assign_dispatch_train_codes(conn, overwrite=args.overwrite)
            conn.rollback()
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"{mode}: groups={result['groups']} rows={result['rows']} updated={result['updated']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
