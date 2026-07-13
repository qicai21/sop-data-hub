"""九三循环车体池近期归列规则。"""
from __future__ import annotations

import sqlite3

from sop_hub.sop.jiusan_cycle_pool import reconcile_recent_cycle_pool


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE wagon_container_shipments (
            car_no TEXT, car_model TEXT, ticketed_at TEXT, project_id TEXT,
            transport_mode_name TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE wagon_body_pool (
            id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, car_no TEXT,
            car_model TEXT, home_cycle_no INTEGER, status TEXT,
            first_seen_date TEXT, last_seen_date TEXT, last_seen_cycle_no INTEGER,
            dispatch_count INTEGER, note TEXT, created_at TEXT, updated_at TEXT
        )"""
    )
    return conn


def _pool(conn: sqlite3.Connection, car_no: str, cycle_no: int) -> None:
    conn.execute(
        "INSERT INTO wagon_body_pool (id,project,ship_name,car_no,home_cycle_no) VALUES (?,?,?,?,?)",
        (car_no, "jiusan", "和谐1", car_no, cycle_no),
    )


def _shipment(conn: sqlite3.Connection, car_no: str, ticketed_at: str) -> None:
    conn.execute(
        "INSERT INTO wagon_container_shipments VALUES (?,?,?,?,?)",
        (car_no, "NX70", ticketed_at, "jiusan", "集装箱"),
    )


def test_reconcile_attaches_small_unknown_tail_to_single_existing_cycle():
    conn = _db()
    for cycle_no in range(1, 5):
        _pool(conn, f"seed{cycle_no}", cycle_no)
    for index in range(40):
        car = f"known{index:02d}"
        _pool(conn, car, 3)
        _shipment(conn, car, "2026-07-11 22:22:00")
    for index in range(5):
        _shipment(conn, f"tail{index:02d}", "2026-07-11 22:24:00")

    result = reconcile_recent_cycle_pool(conn, since="2026-07-10", now="2026-07-13T10:00:00+08:00")

    assert {cycle for _, cycle in result["attached_to_existing_cycle"]} == {3}
    assert len(result["attached_to_existing_cycle"]) == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM wagon_body_pool WHERE project='jiusan' AND home_cycle_no=3"
    ).fetchone()[0] == 46


def test_reconcile_creates_next_cycle_for_full_unknown_train_only():
    conn = _db()
    for cycle_no in range(1, 5):
        _pool(conn, f"seed{cycle_no}", cycle_no)
    for index in range(50):
        _shipment(conn, f"new{index:02d}", "2026-07-10 22:07:00")
    for index in range(5):
        _shipment(conn, f"loose{index:02d}", "2026-07-12 22:07:00")

    result = reconcile_recent_cycle_pool(conn, since="2026-07-10", now="2026-07-13T10:00:00+08:00")

    assert {cycle for _, cycle in result["created_new_cycle"]} == {5}
    assert len(result["created_new_cycle"]) == 50
    assert result["unresolved_car_nos"] == [f"loose{index:02d}" for index in range(5)]
