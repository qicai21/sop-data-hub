"""Functional tests for create_wagon_shipments executor — R45."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sop_hub.sop.create_wagon_shipments import (
    create_wagon_shipments_from_candidates,
)
from sop_hub.sop.departure_text_parser import DepartureCandidate
from sop_hub.sop.shipment_query_window import (
    QueryWindow,
    ShipmentCandidate,
    ShipmentQueryResult,
)

BATCH_ID = "rb_test_001"
SHIP_NAME = "蓝鳍"
PROJECT = "jilin_jingang_jinzhou"


# ── Helpers ────────────────────────────────────────────────────────────

def _make_departure(car_count: int = 18) -> DepartureCandidate:
    return DepartureCandidate(
        message_id="wx_001",
        group_id="GROUP001",
        message_time="2026-05-24T07:16:00Z",
        raw_text=f"煤六 四平铁 蓝鳍 {car_count}节",
        destination="四平",
        car_count=car_count,
        lane_or_track="煤六",
        optional_ship_name="蓝鳍",
        project_id=PROJECT,
        status="complete",
    )


def _make_candidates(count: int = 3, wagon_prefix: str = "16", ydid_prefix: str = "YD") -> list[ShipmentCandidate]:
    return [
        ShipmentCandidate(
            ydid=f"{ydid_prefix}_{i:03d}",
            wagon_no=f"{wagon_prefix}{25000 + i}",
            waybill_no=f"CZ_{ydid_prefix}_{i:03d}",
            container_no=f"TBJU{8000000 + i}",
            origin_station="高桥镇",
            destination_station="四平",
            cargo_name="铁矿粉",
            ticketed_at="2026-05-24 07:14:46",
            departed_at="2026-05-24 11:24:00",
            arrived_at="2026-05-25 03:30:00",
            delivered_at="2026-05-25 20:25:10",
            current_status="交付",
        )
        for i in range(1, count + 1)
    ]


def _make_query_result(
    candidates: list[ShipmentCandidate] | None = None,
) -> ShipmentQueryResult:
    window = QueryWindow.from_reference("2026-05-24 07:16:00")
    cands = candidates if candidates is not None else _make_candidates(3)
    return ShipmentQueryResult(
        window=window,
        origin_station="高桥镇",
        destination_station="四平",
        cargo_name="铁矿粉",
        expected_car_count=3,
        total_candidates=len(cands),
        exact_match_count=len(cands),
        candidates=cands,
    )


def _create_sop_db(db_path: str | Path) -> sqlite3.Connection:
    """Create test sop_agent.db with release_batches and wagon_shipments."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""CREATE TABLE IF NOT EXISTS release_batches (
        id TEXT PRIMARY KEY, batch_key TEXT, project TEXT,
        ship_name TEXT, cargo_name TEXT, destination_station TEXT,
        notice_date TEXT, contract_no TEXT, dispatch_status TEXT DEFAULT 'in_progress',
        dispatch_status_updated_at TEXT, actual_wagon_count INTEGER DEFAULT 0,
        source_json TEXT DEFAULT '{}', searchable_text TEXT DEFAULT ''
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS wagon_shipments (
        id TEXT PRIMARY KEY, departure_id TEXT NOT NULL, batch_id TEXT NOT NULL,
        car_no TEXT, car_model TEXT DEFAULT '', cargo_name TEXT DEFAULT '',
        shipper_name TEXT DEFAULT '', consignee_name TEXT DEFAULT '',
        origin_name TEXT DEFAULT '', destination_name TEXT DEFAULT '',
        ticketed_at TEXT DEFAULT '', departed_at TEXT DEFAULT '',
        arrived_at TEXT DEFAULT '', status_name TEXT DEFAULT '',
        freight_fee REAL DEFAULT 0, detail_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        delivered_at TEXT, confirmed_received_at TEXT, container_no TEXT,
        waybill_no TEXT, project_id TEXT, ship_name TEXT,
        dispatch_status TEXT DEFAULT 'pending', source_message_id TEXT,
        source_group_id TEXT, ydid TEXT, cargo_count INTEGER DEFAULT 0
    )""")

    conn.execute(
        """INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name,
           destination_station, notice_date, dispatch_status, source_json, searchable_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (BATCH_ID, "蓝鳍|铁矿|四平|2026-05-21", PROJECT,
         SHIP_NAME, "铁矿", "四平", "2026-05-21", "in_progress", "{}", "蓝鳍 铁矿 四平"),
    )
    conn.commit()
    return conn


# ── Test 1: candidate_count == car_count → safe_to_apply ───────────────

def test_candidates_equal_car_count_safe_to_apply(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert result.status == "safe_to_apply"
    assert result.safe_to_apply is True
    assert result.planned_insert_count == 3


# ── Test 2: dry-run → no writes ────────────────────────────────────────

def test_pending_review_does_not_write(tmp_path: Path):
    # #98: dry_run 没了,写库自我把关于 status。pending_review(数量对不上、需人工
    # 裁决)不写库 —— 这是唯一保留的"人工 review 前预览"语义。
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),          # 期望 3
        shipment_query_result=_make_query_result(_make_candidates(2)),  # 只有 2
        db_path=db_path,                                  # 无 allow_partial → pending_review
    )

    assert result.status == "pending_review"
    assert result.safe_to_apply is False
    conn = sqlite3.connect(str(db_path))
    cnt = conn.execute("SELECT COUNT(*) FROM wagon_shipments").fetchone()[0]
    conn.close()
    assert cnt == 0   # 自我把关:pending_review 不写


# ── Test 3: apply writes wagon_shipments ───────────────────────────────

def test_apply_writes_wagon_shipments(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert result.status == "safe_to_apply"
    assert result.inserted_count == 3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,)).fetchall()
    assert len(rows) == 3
    assert rows[0]["car_no"]  # not empty
    conn.close()


# ── Test 4: writes waybill_no / wagon_no / container_no ────────────────

def test_apply_writes_waybill_wagon_container(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM wagon_shipments WHERE batch_id=? LIMIT 1", (BATCH_ID,)
    ).fetchone()
    assert row["car_no"] == "1625001"
    assert row["waybill_no"] == "CZ_YD_001"
    assert row["container_no"] == "TBJU8000001"
    conn.close()


# ── Test 5: repeat apply is idempotent / skip ─────────────────────────

def test_repeat_apply_idempotent_skip(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # First apply
    r1 = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert r1.inserted_count == 3

    # Second apply — same candidates should all be skipped
    r2 = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert r2.skipped_existing_count == 3
    assert r2.inserted_count == 0

    conn = sqlite3.connect(str(db_path))
    cnt = conn.execute(
        "SELECT COUNT(*) FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,)
    ).fetchone()[0]
    conn.close()
    assert cnt == 3  # no duplicates


# ── Test 6: 18 cars exist, add 28 more → cumulative 46 ─────────────────

def test_cumulative_departures_18_plus_28(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # First departure: 18 cars
    candidates_18 = _make_candidates(18, wagon_prefix="18", ydid_prefix="A")
    r1 = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(18),
        shipment_query_result=_make_query_result(candidates_18),
        db_path=db_path,
    )
    assert r1.inserted_count == 18

    # Second departure: 28 different cars
    candidates_28 = _make_candidates(28, wagon_prefix="28", ydid_prefix="B")
    r2 = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(28),
        shipment_query_result=_make_query_result(candidates_28),
        db_path=db_path,
    )
    assert r2.inserted_count == 28

    conn = sqlite3.connect(str(db_path))
    cnt = conn.execute(
        "SELECT COUNT(*) FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,)
    ).fetchone()[0]
    conn.close()
    assert cnt == 46


# ── Test 7: cross-batch conflict ───────────────────────────────────────

def test_cross_batch_conflict(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # Insert wagon with the SAME ydid as candidate 1 into ANOTHER release_batch.
    # 去重/冲突按 ydid(发运唯一键),不按 car_no(车号会跨船复用)。
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO wagon_shipments (id, departure_id, batch_id, car_no, ydid)
           VALUES (?, ?, ?, ?, ?)""",
        ("ws_conflict", "dep_other", "rb_other", "1625001", "YD_001"),
    )
    conn.commit()
    conn.close()

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert result.conflict_count >= 1
    # At least one plan should be conflict
    conflicts = [p for p in result.plans if p.action == "conflict_other_batch"]
    assert len(conflicts) >= 1


# ── Test 8: candidate_count < car_count → pending_review ───────────────

def test_fewer_candidates_than_expected(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(10),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )
    assert result.status == "pending_review"
    assert result.safe_to_apply is False


# ── Test 9: candidate_count > car_count, filter reduces to match ───────

def test_filter_reduces_large_candidates(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # 10 candidates, expected 3 cars
    candidates = _make_candidates(10)
    # All candidates have same destination_四平
    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(candidates),
        db_path=db_path,
    )
    # Since all match destination, filter doesn't reduce count
    assert result.planned_insert_count == 10
    # > expected → should be pending_review
    assert result.status == "pending_review"
    assert result.safe_to_apply is False


# ── Test 10: allow_partial ─────────────────────────────────────────────

def test_allow_partial_accepts_fewer(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(10),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        allow_partial=True,
        db_path=db_path,
    )
    assert result.status == "safe_to_apply"
    assert result.safe_to_apply is True
    assert result.planned_insert_count == 3


# ── Test 11: release_batch_id not found ───────────────────────────────

def test_release_batch_not_found(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id="nonexistent",
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(),
        db_path=db_path,
    )
    assert result.status == "not_found"


# ── Test 12: no candidates → no_candidates ─────────────────────────────

def test_no_candidates(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result([]),
        db_path=db_path,
    )
    assert result.status == "no_candidates"
    assert result.planned_insert_count == 0


# ── Test 13: updates release_batches actual_wagon_count ────────────────

def test_updates_release_batch_wagon_count(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (BATCH_ID,)).fetchone()
    assert row["actual_wagon_count"] == 3
    conn.close()


# ── Test 14: cumulative departure updates wagon count ─────────────────

def test_cumulative_updates_wagon_count(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # First 18
    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(18),
        shipment_query_result=_make_query_result(_make_candidates(18, "18")),
        db_path=db_path,
    )
    # Then 28(不同 ydid 前缀 → 是另一批 28 节,与首批 18 节不同 ydid 才会累加;
    #          若沿用同 YD_ 前缀,按 ydid 去重会判为同批重复——那是正确行为,但非本测试意图)
    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(28),
        shipment_query_result=_make_query_result(_make_candidates(28, "28", ydid_prefix="YE")),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (BATCH_ID,)).fetchone()
    assert row["actual_wagon_count"] == 46
    conn.close()


# ── Test 15: does not modify 95306 DB ──────────────────────────────────

def test_does_not_modify_95306_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    _create_sop_db(db_path)

    # Create a fake 95306 DB
    rail_path = tmp_path / "rail.db"
    rail_conn = sqlite3.connect(str(rail_path))
    rail_conn.execute("CREATE TABLE shipments (id TEXT)")
    rail_conn.execute("INSERT INTO shipments VALUES ('test')")
    rail_conn.commit()
    rail_conn.close()

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
        rail_db_path=rail_path,
    )

    # shipments table should be unchanged
    rail_conn2 = sqlite3.connect(str(rail_path))
    cnt = rail_conn2.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    rail_conn2.close()
    assert cnt == 1  # unchanged
