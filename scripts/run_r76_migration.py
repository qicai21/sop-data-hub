#!/usr/bin/env python3
"""R76: shipped_weight tracking + 95306 field sync migration.

Adds 95306 anchor + computed-loading-weight columns to wagon_shipments,
and aggregate weight columns to release_batches. Idempotent.

Usage:
  python scripts/run_r76_migration.py             # dry-run
  python scripts/run_r76_migration.py --apply     # apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "sop_agent.db"

SCHEMA_ADDITIONS: list[tuple[str, str, str]] = [
    # (table, column, sql_type_with_default)
    # wagon_shipments — 95306 sync fields
    ("wagon_shipments", "ydid", "TEXT"),
    ("wagon_shipments", "czydid", "TEXT"),
    ("wagon_shipments", "transport_mode_code", "TEXT"),
    ("wagon_shipments", "transport_mode_name", "TEXT"),
    ("wagon_shipments", "marked_weight", "REAL"),
    ("wagon_shipments", "cargo_count", "INTEGER"),
    ("wagon_shipments", "container_numbers_json", "TEXT"),
    ("wagon_shipments", "accepted_at", "TEXT"),
    ("wagon_shipments", "loaded_at", "TEXT"),
    ("wagon_shipments", "latest_stage_key", "TEXT"),
    ("wagon_shipments", "latest_stage_name", "TEXT"),
    ("wagon_shipments", "latest_event_time", "TEXT"),
    # wagon_shipments — per-wagon computed weight
    ("wagon_shipments", "computed_loading_weight", "REAL"),
    ("wagon_shipments", "weight_rule_basis", "TEXT"),
    # release_batches — aggregate weight
    ("release_batches", "shipped_weight_tons", "REAL DEFAULT 0"),
    ("release_batches", "remaining_weight_tons", "REAL"),
    ("release_batches", "unresolved_wagon_count", "INTEGER DEFAULT 0"),
    ("release_batches", "shipped_weight_last_computed_at", "TEXT"),
]

NEW_INDEXES: list[tuple[str, str]] = [
    ("idx_wagon_shipments_ydid", "CREATE INDEX IF NOT EXISTS idx_wagon_shipments_ydid ON wagon_shipments(ydid)"),
    ("idx_wagon_shipments_project_batch", "CREATE INDEX IF NOT EXISTS idx_wagon_shipments_project_batch ON wagon_shipments(project_id, batch_id)"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--apply", action="store_true", help="apply changes; otherwise dry-run")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    plan: list[str] = []
    skipped: list[str] = []

    for table, col, sql_type in SCHEMA_ADDITIONS:
        cols = existing_columns(conn, table)
        if col in cols:
            skipped.append(f"  - {table}.{col}")
        else:
            stmt = f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"
            plan.append(stmt)

    for name, stmt in NEW_INDEXES:
        plan.append(stmt)

    print(f"[r76] DB: {db_path}")
    print(f"[r76] Already present, skipped ({len(skipped)}):")
    for s in skipped:
        print(s)
    print(f"[r76] Statements to execute ({len(plan)}):")
    for stmt in plan:
        print(f"  - {stmt}")

    if not args.apply:
        print("[r76] dry-run; pass --apply to execute")
        conn.close()
        return 0

    for stmt in plan:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            print(f"[r76] WARN: {stmt} -> {e}")
    conn.commit()

    # Verify post-state
    print("\n[r76] Post-state verification:")
    for table in ("wagon_shipments", "release_batches"):
        cols = existing_columns(conn, table)
        new_cols = [c for t, c, _ in SCHEMA_ADDITIONS if t == table and c in cols]
        print(f"  {table}: {len(cols)} columns total, R76 cols present: {new_cols}")

    conn.close()
    print("[r76] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
