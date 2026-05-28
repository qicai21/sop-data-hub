#!/usr/bin/env python3
"""R39: SOP-driven ordinary freight DB schema migration runner.

Usage:
  Dry-run (default):
    python scripts/run_db_migration.py
    python scripts/run_db_migration.py --dry-run

  Apply:
    python scripts/run_db_migration.py --apply

Idempotent: running --apply multiple times adds only missing columns.
Safe: ALTER TABLE ADD COLUMN only; no DROP, no data modification.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "sop_agent.db"


# ── Schema definitions ──────────────────────────────────────────────────

# Each entry: (table, column_name, column_type, nullable_default)
# column_type includes the full SQL type + optional DEFAULT
SCHEMA_ADDITIONS: list[tuple[str, str, str]] = [
    # wagon_shipments — tracking phase fields
    ("wagon_shipments", "delivered_at", "TEXT"),
    ("wagon_shipments", "confirmed_received_at", "TEXT"),
    ("wagon_shipments", "container_no", "TEXT"),
    ("wagon_shipments", "waybill_no", "TEXT"),
    # release_batches — freight detail fields
    ("release_batches", "order_identifier", "TEXT"),
    ("release_batches", "cargo_name_detail", "TEXT"),
    ("release_batches", "confirmed_received_at", "TEXT"),
]

# New tables to CREATE IF NOT EXISTS
NEW_TABLES: dict[str, str] = {
    "dashboard_state": """
        CREATE TABLE IF NOT EXISTS dashboard_state (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            release_batch_id TEXT,
            total_wagon_count INTEGER DEFAULT 0,
            dispatched_count INTEGER DEFAULT 0,
            arrived_count INTEGER DEFAULT 0,
            delivered_count INTEGER DEFAULT 0,
            confirmed_received_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            last_updated_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
}


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class MigrationAction:
    table: str
    action: str  # "add_column" | "create_table" | "skip"
    column_name: str = ""
    column_type: str = ""
    reason: str = ""


@dataclass
class MigrationResult:
    dry_run: bool
    db_path: str
    current_schema_version: str = "pre-r39"
    planned_actions: list[MigrationAction] = field(default_factory=list)
    applied_actions: list[MigrationAction] = field(default_factory=list)
    skipped_actions: list[MigrationAction] = field(default_factory=list)
    added_columns: list[str] = field(default_factory=list)
    skipped_columns: list[str] = field(default_factory=list)
    new_tables: list[str] = field(default_factory=list)
    skipped_tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "db_path": self.db_path,
            "current_schema_version": self.current_schema_version,
            "planned_actions": [
                {
                    "table": a.table,
                    "action": a.action,
                    "column_name": a.column_name,
                    "column_type": a.column_type,
                    "reason": a.reason,
                }
                for a in self.planned_actions
            ],
            "applied_actions": [
                {
                    "table": a.table,
                    "action": a.action,
                    "column_name": a.column_name,
                    "column_type": a.column_type,
                    "reason": a.reason,
                }
                for a in self.applied_actions
            ],
            "skipped_actions": [
                {
                    "table": a.table,
                    "action": a.action,
                    "column_name": a.column_name,
                    "column_type": a.column_type,
                    "reason": a.reason,
                }
                for a in self.skipped_actions
            ],
            "added_columns": self.added_columns,
            "skipped_columns": self.skipped_columns,
            "new_tables": self.new_tables,
            "skipped_tables": self.skipped_tables,
            "warnings": self.warnings,
            "error": self.error,
        }


# ── Core logic ──────────────────────────────────────────────────────────

def run_migration(db_path: Path, *, dry_run: bool = True) -> MigrationResult:
    """Run the R39 schema migration."""

    result = MigrationResult(
        dry_run=dry_run,
        db_path=str(db_path),
    )

    if not db_path.exists():
        result.error = f"Database not found: {db_path}"
        return result

    conn = sqlite3.connect(str(db_path))
    try:
        # ── 1. Plan actions ───────────────────────────────────────────
        result.planned_actions = _plan_actions(conn)
        result.skipped_actions = [
            a for a in result.planned_actions if a.action == "skip"
        ]
        result.skipped_columns = [
            f"{a.table}.{a.column_name}"
            for a in result.skipped_actions
            if a.column_name
        ]
        result.skipped_tables = [
            a.table
            for a in result.skipped_actions
            if a.action == "skip" and not a.column_name
        ]

        if dry_run:
            # Report what WOULD be done
            would_apply = [a for a in result.planned_actions if a.action != "skip"]
            result.added_columns = [
                f"{a.table}.{a.column_name}"
                for a in would_apply
                if a.action == "add_column"
            ]
            result.new_tables = [
                a.table
                for a in would_apply
                if a.action == "create_table"
            ]
            return result

        # ── 2. Execute actions ────────────────────────────────────────
        for action in result.planned_actions:
            if action.action == "skip":
                continue

            result.applied_actions.append(action)

            if action.action == "add_column":
                sql = (
                    f"ALTER TABLE {action.table} "
                    f"ADD COLUMN {action.column_name} {action.column_type}"
                )
                conn.execute(sql)
                result.added_columns.append(
                    f"{action.table}.{action.column_name}"
                )

            elif action.action == "create_table":
                table_sql = NEW_TABLES.get(action.table, "")
                if table_sql:
                    conn.execute(table_sql)
                    result.new_tables.append(action.table)
                else:
                    result.warnings.append(
                        f"No CREATE SQL for table: {action.table}"
                    )

        conn.commit()
        result.current_schema_version = "r39"

    except Exception as exc:
        result.error = str(exc)
        if not dry_run:
            conn.rollback()
    finally:
        conn.close()

    return result


def _plan_actions(conn: sqlite3.Connection) -> list[MigrationAction]:
    """Plan schema changes — idempotent: skip already-existing columns/tables."""
    actions: list[MigrationAction] = []

    # Check existing tables
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # ── Column additions ──────────────────────────────────────────────
    table_columns: dict[str, set[str]] = {}
    for table_name in {s[0] for s in SCHEMA_ADDITIONS}:
        if table_name not in existing_tables:
            actions.append(MigrationAction(
                table=table_name,
                action="skip",
                reason=f"table does not exist — skip column additions",
            ))
            continue

        if table_name not in table_columns:
            cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            table_columns[table_name] = {row[1] for row in cols}

    for table_name, col_name, col_type in SCHEMA_ADDITIONS:
        if table_name not in table_columns:
            continue  # Already handled above as table-not-exists skip

        if col_name in table_columns[table_name]:
            actions.append(MigrationAction(
                table=table_name,
                action="skip",
                column_name=col_name,
                column_type=col_type,
                reason="column already exists",
            ))
        else:
            actions.append(MigrationAction(
                table=table_name,
                action="add_column",
                column_name=col_name,
                column_type=col_type,
                reason="column missing — will add",
            ))

    # ── Table creations ───────────────────────────────────────────────
    for table_name in NEW_TABLES:
        if table_name in existing_tables:
            actions.append(MigrationAction(
                table=table_name,
                action="skip",
                reason="table already exists",
            ))
        else:
            actions.append(MigrationAction(
                table=table_name,
                action="create_table",
                reason="table missing — will create",
            ))

    return actions


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="R39: Ordinary freight DB schema migration"
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to sop_agent.db (default: {DEFAULT_DB_PATH})",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Plan migrations without executing (default)",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        dest="apply",
        help="Execute the migration",
    )

    args = parser.parse_args()
    db_path = Path(args.db_path)
    dry_run = not args.apply

    result = run_migration(db_path, dry_run=dry_run)

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

    if result.error:
        print(f"\n❌ Error: {result.error}", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        if result.added_columns:
            print(
                f"\n[Dry-run] Would add {len(result.added_columns)} columns: "
                f"{result.added_columns}",
                file=sys.stderr,
            )
        if result.new_tables:
            print(
                f"\n[Dry-run] Would create {len(result.new_tables)} tables: "
                f"{result.new_tables}",
                file=sys.stderr,
            )
        if not result.added_columns and not result.new_tables:
            print("\n[Dry-run] Schema up to date — nothing to apply.", file=sys.stderr)
    else:
        if result.added_columns:
            print(
                f"\n[Apply] Added {len(result.added_columns)} columns: "
                f"{result.added_columns}",
                file=sys.stderr,
            )
        if result.new_tables:
            print(
                f"\n[Apply] Created {len(result.new_tables)} tables: "
                f"{result.new_tables}",
                file=sys.stderr,
            )

    if result.warnings:
        for w in result.warnings:
            print(f"⚠️  {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
