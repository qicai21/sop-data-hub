#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"


def _unique_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='bulk_loading_notice_wagon'"
    ).fetchone()
    return str(row[0] or "") if row else ""


def main() -> None:
    conn = sqlite3.connect(str(DB))
    try:
        sql = _unique_sql(conn)
        if "UNIQUE(project, notice_date, track, car_seq, ship_name)" in sql:
            print("SKIP: bulk_loading_notice_wagon unique constraint already includes ship_name")
            return

        conn.execute("BEGIN")
        conn.execute("ALTER TABLE bulk_loading_notice_wagon RENAME TO bulk_loading_notice_wagon_old")
        conn.execute(
            """
            CREATE TABLE bulk_loading_notice_wagon (
              id TEXT PRIMARY KEY,
              project TEXT NOT NULL,
              notice_date TEXT NOT NULL,
              track TEXT,
              total_cars INTEGER,
              car_seq INTEGER,
              car_no TEXT NOT NULL,
              car_model TEXT,
              ship_name TEXT NOT NULL,
              lot TEXT DEFAULT 'lot02',
              ydid TEXT,
              destination TEXT,
              consignee TEXT,
              source_ref TEXT,
              created_at TEXT,
              UNIQUE(project, notice_date, track, car_seq, ship_name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bulk_loading_notice_wagon (
              id, project, notice_date, track, total_cars, car_seq, car_no, car_model,
              ship_name, lot, ydid, destination, consignee, source_ref, created_at
            )
            SELECT
              id, project, notice_date, track, total_cars, car_seq, car_no, car_model,
              ship_name, lot, ydid, destination, consignee, source_ref, created_at
            FROM bulk_loading_notice_wagon_old
            """
        )
        conn.execute("DROP TABLE bulk_loading_notice_wagon_old")
        conn.commit()
        print("APPLIED: bulk_loading_notice_wagon unique(project, notice_date, track, car_seq, ship_name)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
