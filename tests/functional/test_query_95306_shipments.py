"""Functional tests for 95306 shipment query window executor — R42."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sop_hub.sop.shipment_query_window import (
    QueryWindow,
    ShipmentCandidate,
    ShipmentQueryResult,
)
from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window


# ── Test helpers ────────────────────────────────────────────────────────

def _create_test_rail_db(db_path: str | Path) -> sqlite3.Connection:
    """Create a test 95306 shipments table with sample data."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            ydid TEXT PRIMARY KEY,
            czydid TEXT,
            car_no TEXT,
            car_model TEXT,
            cargo_name TEXT,
            origin_name TEXT,
            destination_name TEXT,
            container_no_raw TEXT,
            ticketed_at TEXT,
            departed_at TEXT,
            arrived_at TEXT,
            delivered_at TEXT,
            status_name TEXT,
            latest_stage_name TEXT
        )
    """)

    # Sample data: 高桥镇 → 四平, 铁矿粉, ~2026-05-24
    _sample_rows = [
        ("YD_001", "CZ_001", "1625630", "NX70", "铁矿粉", "高桥镇", "四平",
         "TBJU8451433/TBJU8416052",
         "2026-05-24 07:14:46", "2026-05-24 11:24:00",
         "2026-05-25 03:30:00", "2026-05-25 20:25:10",
         "货物已交付", "交付"),
        ("YD_002", "CZ_002", "1512030", "NX70", "铁矿粉", "高桥镇", "四平",
         "TBJU0677126/TBJU5879500",
         "2026-05-24 07:14:51", "2026-05-24 11:24:00",
         "2026-05-25 03:30:00", "2026-05-25 20:25:10",
         "货物已交付", "交付"),
        ("YD_003", "CZ_003", "1511993", "NX70", "铁矿粉", "高桥镇", "四平",
         "TBJU6917836/TBJU8559963",
         "2026-05-24 07:14:55", "2026-05-24 11:24:00",
         "2026-05-25 03:30:00", "2026-05-25 20:25:10",
         "货物已交付", "交付"),
        # Different cargo: 煤炭 (should be excluded by cargo_name filter)
        ("YD_004", "CZ_004", "1111111", "C70", "煤炭", "高桥镇", "四平",
         "",
         "2026-05-24 07:15:00", "2026-05-24 12:00:00",
         "2026-05-25 04:00:00", "2026-05-25 21:00:00",
         "货物已交付", "交付"),
        # Different destination: 朝阳西
        ("YD_005", "CZ_005", "2222222", "NX70", "铁矿粉", "高桥镇", "朝阳西",
         "",
         "2026-05-24 07:15:30", "2026-05-24 12:30:00",
         "2026-05-25 05:00:00", "",
         "到站", "到站"),
        # Outside time window
        ("YD_006", "CZ_006", "3333333", "NX70", "铁矿粉", "高桥镇", "四平",
         "",
         "2026-05-24 09:00:00", "", "", "", "发车", "发车"),
    ]

    for row in _sample_rows:
        conn.execute(
            "INSERT INTO shipments (ydid, czydid, car_no, car_model, cargo_name, "
            "origin_name, destination_name, container_no_raw, ticketed_at, "
            "departed_at, arrived_at, delivered_at, status_name, latest_stage_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    return conn


# ── Test 1: QueryWindow generates correctly ────────────────────────────

def test_query_window_generation():
    """QueryWindow.from_reference should compute correct start/end times."""
    w = QueryWindow.from_reference(
        "2026-05-24 07:16:00",
        window_before_minutes=60,
        window_after_minutes=60,
    )
    assert w.start_time == "2026-05-24 06:16:00"
    assert w.end_time == "2026-05-24 08:16:00"
    assert w.reference_time == "2026-05-24 07:16:00"
    assert w.window_before_minutes == 60
    assert w.window_after_minutes == 60


# ── Test 2: 60-minute window before/after ──────────────────────────────

def test_window_60_before_60_after(tmp_path: Path):
    """Query with ±60 min window should return candidates within that range."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    assert result.total_candidates >= 3  # YD_001, YD_002, YD_003
    # YD_006 (09:00) is outside window
    car_nos = [c.wagon_no for c in result.candidates]
    assert "3333333" not in car_nos  # outside window


# ── Test 3: Query returns ShipmentCandidates ────────────────────────────

def test_query_returns_shipment_candidates(tmp_path: Path):
    """Query should return ShipmentCandidate objects with correct fields."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    assert len(result.candidates) >= 1
    c = result.candidates[0]
    assert isinstance(c, ShipmentCandidate)
    assert c.origin_station == "高桥镇"
    assert c.destination_station == "四平"
    assert c.wagon_no  # not empty
    assert c.ydid  # not empty
    assert c.ticketed_at  # not empty
    assert c.cargo_name  # not empty


# ── Test 4: cargo_name filter ──────────────────────────────────────────

def test_cargo_name_filter(tmp_path: Path):
    """When cargo_name is provided, only matching shipments returned."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        cargo_name="铁矿粉",
        rail_db_path=db_path,
    )

    # Should include 铁矿粉, exclude 煤炭
    cargo_names = [c.cargo_name for c in result.candidates]
    assert all("铁矿粉" in cn for cn in cargo_names)
    assert not any("煤炭" in cn for cn in cargo_names)


# ── Test 5: expected_car_count output ──────────────────────────────────

def test_expected_car_count_output(tmp_path: Path):
    """When expected_car_count is provided, summary reflects it."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    # With the test data, we have YD_001-YD_003 for 高桥镇→四平 铁矿粉
    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        cargo_name="铁矿粉",
        expected_car_count=3,
        rail_db_path=db_path,
    )

    assert result.expected_car_count == 3
    assert result.exact_match_count == 3
    assert result.ambiguous_count >= 0  # extra candidates if any


# ── Test 6: No results ─────────────────────────────────────────────────

def test_no_results(tmp_path: Path):
    """Query with non-matching criteria should return zero candidates."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    result = query_95306_shipments_by_window(
        origin_station="火星站",
        destination_station="月球站",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    assert result.total_candidates == 0
    assert result.candidates == []
    assert result.exact_match_count == 0


# ── Test 7: Multiple candidates ────────────────────────────────────────

def test_multiple_candidates(tmp_path: Path):
    """Query should return all candidates within the window."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    assert result.total_candidates >= 3
    # All candidates should have unique ydid
    ydids = [c.ydid for c in result.candidates]
    assert len(ydids) == len(set(ydids))


# ── Test 8: Read-only verification ─────────────────────────────────────

def test_read_only_does_not_modify_db(tmp_path: Path):
    """Query should NOT modify the 95306 DB (read-only)."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    # Record row count before
    before = sqlite3.connect(str(db_path))
    before_count = before.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    before.close()

    query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    after = sqlite3.connect(str(db_path))
    after_count = after.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    after.close()

    assert before_count == after_count
    assert before_count == 6  # all 6 test rows intact


# ── Test 9: Does not modify sop_agent.db ───────────────────────────────

def test_does_not_modify_sop_agent_db(tmp_path: Path):
    """Query should NOT touch sop_agent.db at all."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    # Create a sop_agent.db with a known row
    sop_path = tmp_path / "sop_agent.db"
    sop_conn = sqlite3.connect(str(sop_path))
    sop_conn.execute("CREATE TABLE test_table (id TEXT, value TEXT)")
    sop_conn.execute("INSERT INTO test_table VALUES ('1', 'before')")
    sop_conn.commit()
    sop_conn.close()

    query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        rail_db_path=db_path,
    )

    # Verify sop_agent.db is untouched
    sop_conn2 = sqlite3.connect(str(sop_path))
    row = sop_conn2.execute("SELECT value FROM test_table WHERE id='1'").fetchone()
    sop_conn2.close()
    assert row is not None
    assert row[0] == "before"


# ── Test 10: Does not modify 95306 DB rows ─────────────────────────────

def test_does_not_modify_95306_db_content(tmp_path: Path):
    """Query should not change any shipment row data."""
    db_path = tmp_path / "test_rail.db"
    _create_test_rail_db(db_path)

    # Snapshot all rows before query
    before_conn = sqlite3.connect(str(db_path))
    before_conn.row_factory = sqlite3.Row
    before_rows = [
        dict(row) for row in before_conn.execute("SELECT * FROM shipments ORDER BY ydid").fetchall()
    ]
    before_conn.close()

    query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        window_before_minutes=60,
        window_after_minutes=60,
        rail_db_path=db_path,
    )

    after_conn = sqlite3.connect(str(db_path))
    after_conn.row_factory = sqlite3.Row
    after_rows = [
        dict(row) for row in after_conn.execute("SELECT * FROM shipments ORDER BY ydid").fetchall()
    ]
    after_conn.close()

    assert before_rows == after_rows


# ── Test: ShipmentCandidate to_dict ────────────────────────────────────

def test_shipment_candidate_to_dict():
    """ShipmentCandidate.to_dict() should return a serializable dict."""
    c = ShipmentCandidate(
        ydid="YD_TEST",
        wagon_no="1625630",
        waybill_no="CZ_TEST",
        container_no="TBJU8451433",
        origin_station="高桥镇",
        destination_station="四平",
        cargo_name="铁矿粉",
        ticketed_at="2026-05-24 07:14:46",
        departed_at="2026-05-24 11:24:00",
        arrived_at="2026-05-25 03:30:00",
        delivered_at="2026-05-25 20:25:10",
        current_status="交付",
    )
    d = c.to_dict()
    assert d["ydid"] == "YD_TEST"
    assert d["wagon_no"] == "1625630"
    assert d["current_status"] == "交付"


# ── Test: ShipmentQueryResult to_dict ──────────────────────────────────

def test_shipment_query_result_to_dict():
    """ShipmentQueryResult.to_dict() should be JSON-serializable."""
    import json

    w = QueryWindow.from_reference("2026-05-24 07:16:00")
    result = ShipmentQueryResult(
        window=w,
        origin_station="高桥镇",
        destination_station="四平",
        cargo_name="铁矿粉",
        expected_car_count=3,
        total_candidates=3,
        exact_match_count=3,
        ambiguous_count=0,
        candidates=[
            ShipmentCandidate(
                ydid="YD_001", wagon_no="1625630",
                origin_station="高桥镇", destination_station="四平",
                cargo_name="铁矿粉", ticketed_at="2026-05-24 07:14:46",
            ),
        ],
    )
    d = result.to_dict()
    # Should be JSON-serializable
    json_str = json.dumps(d, ensure_ascii=False)
    assert "高桥镇" in json_str


# ── Test: QueryWindow to_dict ──────────────────────────────────────────

def test_query_window_to_dict():
    """QueryWindow.to_dict() should be correct."""
    w = QueryWindow.from_reference(
        "2026-05-24 07:16:00",
        window_before_minutes=30,
        window_after_minutes=45,
    )
    d = w.to_dict()
    assert d["start_time"] == "2026-05-24 06:46:00"
    assert d["end_time"] == "2026-05-24 08:01:00"
    assert d["window_before_minutes"] == 30
    assert d["window_after_minutes"] == 45


# ── Test: empty database path returns empty result ─────────────────────

def test_missing_db_returns_empty_result():
    """When the 95306 DB doesn't exist, return empty result (no crash)."""
    result = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time="2026-05-24 07:15:00",
        rail_db_path="/nonexistent/path/95306.db",
    )
    assert result.total_candidates == 0
    assert result.candidates == []
