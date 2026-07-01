#!/usr/bin/env python3
"""R81: fee-domain schema migration runner.

Idempotent:
- CREATE TABLE IF NOT EXISTS 天然幂等
- ALTER TABLE ADD COLUMN 用 PRAGMA table_info 预查,已存在则跳过

Usage:
  python scripts/run_r81_fee_domain_migration.py            # dry-run
  python scripts/run_r81_fee_domain_migration.py --apply    # 真正提交
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "sop_agent.db"
MIGRATION_SQL = REPO_ROOT / "migrations" / "20260701_r81_fee_domain_schema.sql"


def column_exists(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def apply_migration(db_path: Path, apply: bool) -> None:
    if not MIGRATION_SQL.exists():
        sys.exit(f"missing migration file: {MIGRATION_SQL}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    sql_text = MIGRATION_SQL.read_text()

    safe_lines: list[str] = []
    skipped_alters: list[str] = []
    for stmt in sql_text.split(";"):
        s = stmt.strip()
        if not s:
            continue
        if s.startswith("--"):
            safe_lines.append(stmt + ";")
            continue
        if s.upper().startswith("ALTER TABLE"):
            parts = s.split()
            try:
                t_idx = parts.index("TABLE") + 1
                table = parts[t_idx]
                c_idx = parts.index("COLUMN") + 1
                col = parts[c_idx]
            except (ValueError, IndexError):
                safe_lines.append(stmt + ";")
                continue
            if column_exists(cur, table, col):
                skipped_alters.append(f"{table}.{col}")
                continue
            safe_lines.append(stmt + ";")
        else:
            safe_lines.append(stmt + ";")

    final_sql = "\n".join(safe_lines)
    print("=== SQL to run ===")
    print(final_sql[:4000] + ("..." if len(final_sql) > 4000 else ""))
    print(f"\n=== skipped ALTER (column 已存在): {skipped_alters} ===")

    if apply:
        cur.executescript(final_sql)
        conn.commit()
        print("\nAPPLIED ✓")
    else:
        conn.rollback()
        print("\nDRY-RUN(加 --apply 真正执行)")

    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args()
    apply_migration(args.db, args.apply)


if __name__ == "__main__":
    main()
