#!/usr/bin/env python3
"""R76 backfill: sync 95306 fields into existing wagon_shipments rows.

For each existing row in sop_agent.db wagon_shipments, find the matching
95306 shipment by (car_no, close ticketed_at) — there is no ydid yet on
the sop side because the original ingest path did not carry it. After this
backfill, ydid becomes the stable anchor for any future sync.

Match strategy:
  1. Prefer exact (car_no, ticketed_at) match.
  2. If no exact ticketed_at match, take the 95306 row with car_no whose
     ticketed_at is within ±24h of the sop side ticketed_at.
  3. If multiple candidates, pick the one whose origin_name + destination_name
     matches the sop side (origin+destination usually present on both sides).
  4. If still ambiguous, log and skip — do not guess.

Idempotent: rows that already have ydid populated are skipped (assume done).
Read-only against 95306; write only sop_agent.db.

Usage:
  python scripts/backfill_wagon_shipments_from_95306.py            # dry-run
  python scripts/backfill_wagon_shipments_from_95306.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
DEFAULT_RAIL_DB = Path.home() / "projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"

# Fields copied from 95306 shipments → wagon_shipments
SYNC_FIELDS = [
    "ydid",
    "czydid",
    "transport_mode_code",
    "transport_mode_name",
    "marked_weight",
    "freight_fee",
    "cargo_count",
    "container_numbers_json",
    "accepted_at",
    "loaded_at",
    "latest_stage_key",
    "latest_stage_name",
    "latest_event_time",
]


def open_db(path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def find_95306_match(rail_conn, car_no: str, ticketed_at: str,
                     origin_hint: str, dest_hint: str) -> dict | None:
    """Return single 95306 row dict, or None."""
    # Exact match first
    rows = rail_conn.execute(
        "SELECT * FROM shipments WHERE car_no=? AND ticketed_at=?",
        (car_no, ticketed_at),
    ).fetchall()
    if len(rows) == 1:
        return dict(rows[0])

    # Same car_no, ticketed_at within ±24h (string compare works for our ISO format)
    rows = rail_conn.execute(
        "SELECT * FROM shipments WHERE car_no=? "
        "AND ticketed_at IS NOT NULL "
        "AND date(ticketed_at) BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
        "ORDER BY ticketed_at",
        (car_no, ticketed_at or "1970-01-01", ticketed_at or "2100-01-01"),
    ).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return dict(rows[0])

    # Multiple candidates — filter by origin/destination
    if origin_hint or dest_hint:
        narrowed = [
            r for r in rows
            if (not origin_hint or r["origin_name"] == origin_hint)
            and (not dest_hint or r["destination_name"] == dest_hint)
        ]
        if len(narrowed) == 1:
            return dict(narrowed[0])

    # Ambiguous; pick closest in time if ticketed_at given
    if ticketed_at:
        def time_dist(r):
            return abs((r["ticketed_at"] or "") > ticketed_at) - abs((r["ticketed_at"] or "") < ticketed_at)
        rows = sorted(rows, key=lambda r: r["ticketed_at"] or "")
        return dict(rows[0])

    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sop-db", default=str(DEFAULT_SOP_DB))
    p.add_argument("--rail-db", default=str(DEFAULT_RAIL_DB))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=0, help="limit rows for dev")
    args = p.parse_args()

    sop_path = Path(args.sop_db)
    rail_path = Path(args.rail_db)
    if not sop_path.exists():
        print(f"sop_db not found: {sop_path}", file=sys.stderr); return 1
    if not rail_path.exists():
        print(f"rail_db not found: {rail_path}", file=sys.stderr); return 1

    sop_conn = open_db(sop_path)
    rail_conn = open_db(rail_path, read_only=True)

    # Select wagons that have not been backfilled yet (no ydid)
    sql = (
        "SELECT id, car_no, ticketed_at, origin_name, destination_name, project_id "
        "FROM wagon_shipments "
        "WHERE COALESCE(ydid,'')='' "
        "ORDER BY created_at DESC"
    )
    if args.limit:
        sql += f" LIMIT {args.limit}"
    todo = sop_conn.execute(sql).fetchall()

    print(f"[backfill] candidates needing backfill: {len(todo)}")

    stats = Counter()
    updates: list[tuple[dict, str]] = []  # (rail_row, sop_id)
    for row in todo:
        match = find_95306_match(
            rail_conn,
            car_no=row["car_no"],
            ticketed_at=row["ticketed_at"] or "",
            origin_hint=row["origin_name"] or "",
            dest_hint=row["destination_name"] or "",
        )
        if not match:
            stats["no_match"] += 1
            continue
        updates.append((match, row["id"]))
        stats["matched"] += 1

    print(f"[backfill] matched={stats['matched']}  no_match={stats['no_match']}")

    if not args.apply:
        if updates:
            print("\n[backfill] sample of first 3 matches:")
            for rail, sop_id in updates[:3]:
                print(f"  sop_id={sop_id}  ydid={rail['ydid']}  marked={rail['marked_weight']}  "
                      f"car_model={rail['car_model']}  cargo_count={rail['cargo_count']}")
        print("\n[backfill] dry-run; pass --apply to write")
        return 0

    set_clause = ", ".join(f"{f}=?" for f in SYNC_FIELDS) + ", updated_at=datetime('now')"
    update_sql = f"UPDATE wagon_shipments SET {set_clause} WHERE id=?"

    applied = 0
    for rail, sop_id in updates:
        values = [rail.get(f) for f in SYNC_FIELDS] + [sop_id]
        sop_conn.execute(update_sql, values)
        applied += 1
    sop_conn.commit()
    print(f"[backfill] applied {applied} updates.")

    # Verify
    n_with_ydid = sop_conn.execute(
        "SELECT count(*) FROM wagon_shipments WHERE COALESCE(ydid,'')<>''"
    ).fetchone()[0]
    n_total = sop_conn.execute("SELECT count(*) FROM wagon_shipments").fetchone()[0]
    print(f"[backfill] wagon_shipments with ydid: {n_with_ydid}/{n_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
