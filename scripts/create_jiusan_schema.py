#!/usr/bin/env python3
"""
Create 九三大豆循环运输资源账系统 empty database (Phase 1-2)

Usage:
    python3 scripts/create_jiusan_schema.py

Creates: data/jiusan_cycle.db with Phase 1-2 tables.
Phase 3 tables (resource_pool, cycle_train_compositions) are excluded.
"""
import sqlite3
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "jiusan_cycle_schema.sql"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jiusan_cycle.db"


def create_database(db_path: Path = DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # If DB already exists, just check tables
    if db_path.exists():
        existing = _list_tables(db_path)
        if existing:
            print(f"Database already exists at {db_path}")
            print(f"Existing tables: {', '.join(existing)}")
            print("Use --force to recreate.")
            return

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    if SCHEMA_PATH.exists():
        sql = SCHEMA_PATH.read_text()
        conn.executescript(sql)
        print(f"Schema loaded from {SCHEMA_PATH}")
    else:
        print(f"Schema file not found at {SCHEMA_PATH}", file=sys.stderr)
        sys.exit(1)

    conn.commit()
    tables = _list_tables(db_path)
    print(f"Database created at {db_path}")
    print(f"Tables created: {', '.join(tables)} ({len(tables)} total)")
    conn.close()


def _list_tables(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def drop_database(db_path: Path = DB_PATH):
    if db_path.exists():
        db_path.unlink()
        print(f"Dropped {db_path}")


if __name__ == "__main__":
    if "--force" in sys.argv:
        drop_database()
    create_database()
