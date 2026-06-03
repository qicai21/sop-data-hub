"""R45.1: DB boundary tests — verify create_wagon_shipments never writes 95306 DB."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from sop_hub.sop.create_wagon_shipments import (
    create_wagon_shipments_from_candidates,
)
from sop_hub.sop.departure_text_parser import DepartureCandidate
from sop_hub.sop.shipment_query_window import (
    QueryWindow,
    ShipmentCandidate,
    ShipmentQueryResult,
)

BATCH_ID = "rb_boundary_001"
PROJECT = "jilin_jingang_jinzhou"


def _make_departure(car_count: int = 3) -> DepartureCandidate:
    return DepartureCandidate(
        message_id="wx_boundary",
        group_id="GROUP001",
        message_time="2026-05-24T07:16:00Z",
        raw_text=f"蓝鳍 {car_count}节",
        destination="四平",
        car_count=car_count,
        optional_ship_name="蓝鳍",
        project_id=PROJECT,
        status="complete",
    )


def _make_candidates(count: int = 3) -> list[ShipmentCandidate]:
    return [
        ShipmentCandidate(
            ydid=f"B{hashlib.sha1(str(i).encode()).hexdigest()[:8]}",
            wagon_no=f"99{25000 + i}",
            waybill_no=f"CW_{i:03d}",
            container_no=f"TBJU{9000000 + i}",
            origin_station="高桥镇",
            destination_station="四平",
            cargo_name="铁矿粉",
            ticketed_at="2026-05-24 07:14:46",
            departed_at="2026-05-24 11:24:00",
            arrived_at="2026-05-25 03:30:00",
            current_status="交付",
        )
        for i in range(1, count + 1)
    ]


def _make_query_result(candidates: list[ShipmentCandidate]) -> ShipmentQueryResult:
    window = QueryWindow.from_reference("2026-05-24 07:16:00")
    return ShipmentQueryResult(
        window=window,
        origin_station="高桥镇",
        destination_station="四平",
        candidates=candidates,
        total_candidates=len(candidates),
        exact_match_count=len(candidates),
    )


def _create_sop_db(db_path: str | Path) -> sqlite3.Connection:
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
        source_group_id TEXT
    )""")
    conn.execute(
        "INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name, "
        "destination_station, notice_date, dispatch_status, source_json, searchable_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (BATCH_ID, "test|boundary", PROJECT, "蓝鳍", "铁矿", "四平",
         "2026-05-21", "in_progress", "{}", "test"),
    )
    conn.commit()
    return conn


def _create_fake_95306_db(db_path: str | Path, initial_count: int = 10) -> Path:
    """Create a fake 95306 DB with known row count for boundary verification."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS shipments (id INTEGER PRIMARY KEY, car_no TEXT)")
    for i in range(initial_count):
        conn.execute("INSERT INTO shipments (car_no) VALUES (?)", (f"car_{i:05d}",))
    # Also create shipment_release_batch_matches as the real 95306 DB has it
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
            id TEXT PRIMARY KEY, release_batch_id TEXT, shipment_ydid TEXT
        )
    """)
    # One existing match row
    conn.execute(
        "INSERT INTO shipment_release_batch_matches (id, release_batch_id, shipment_ydid) "
        "VALUES (?, ?, ?)", ("existing_001", "other_batch", "YD_FAKE")
    )
    conn.commit()
    conn.close()
    return Path(db_path)


# ── Test 1: 95306 DB ships表行数不变 ──────────────────────────────────

def test_95306_db_row_count_unchanged(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    rail_path = tmp_path / "rail.db"
    _create_fake_95306_db(rail_path, initial_count=10)

    before = sqlite3.connect(str(rail_path))
    before_count = before.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    before.close()

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
        rail_db_path=rail_path,
    )

    after = sqlite3.connect(str(rail_path))
    after_count = after.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    after.close()

    assert before_count == after_count == 10


# ── Test 2: 95306 DB 中 shipment_release_batch_matches 行数不变 ───────

def test_95306_db_matches_unchanged(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    rail_path = tmp_path / "rail.db"
    _create_fake_95306_db(rail_path)

    before = sqlite3.connect(str(rail_path))
    before_count = before.execute(
        "SELECT COUNT(*) FROM shipment_release_batch_matches"
    ).fetchone()[0]
    before.close()

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
        rail_db_path=rail_path,
    )

    after = sqlite3.connect(str(rail_path))
    after_count = after.execute(
        "SELECT COUNT(*) FROM shipment_release_batch_matches"
    ).fetchone()[0]
    after.close()

    assert before_count == after_count == 1  # only the pre-existing row


# ── Test 3: sop_agent.db 有 shipment_release_batch_matches ─────────────

def test_sop_db_has_matches_table(tmp_path: Path):
    db_path = tmp_path / "sop.db"
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
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='shipment_release_batch_matches'"
    ).fetchone()
    assert row is not None, "shipment_release_batch_matches table not found in sop_agent.db"


# ── Test 4: match 记录写入 sop_agent.db ───────────────────────────────

def test_match_records_in_sop_db(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM shipment_release_batch_matches WHERE release_batch_id=?",
        (BATCH_ID,),
    ).fetchall()
    assert len(rows) == 3
    for r in rows:
        assert r["release_batch_id"] == BATCH_ID
        assert r["wagon_shipment_id"]
        assert r["ydid"]
        assert r["match_source"] == "departure_text_match"
    conn.close()


# ── Test 5: wagon_shipments 写入 sop_agent.db ─────────────────────────

def test_wagon_shipments_in_sop_db(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    cnt = conn.execute(
        "SELECT COUNT(*) FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,)
    ).fetchone()[0]
    conn.close()
    assert cnt == 3


# ── Test 6: 95306 DB 文件校验和不变 ──────────────────────────────────

def test_95306_db_checksum_unchanged(tmp_path: Path):
    import hashlib as hl

    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    rail_path = tmp_path / "rail.db"
    _create_fake_95306_db(rail_path)

    # Compute checksum before
    before_hash = hl.sha256(rail_path.read_bytes()).hexdigest()

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
        rail_db_path=rail_path,
    )

    after_hash = hl.sha256(rail_path.read_bytes()).hexdigest()
    assert before_hash == after_hash, (
        f"95306 DB checksum changed! {before_hash} → {after_hash}"
    )


# ── Test 7: 95306 DB 不存在不崩溃 ────────────────────────────────────

def test_missing_rail_db_no_crash(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    result = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
        rail_db_path="/nonexistent/95306.db",
    )
    assert result.status == "safe_to_apply"
    assert result.inserted_count == 3


# ── Test 8: 重复 apply 幂等（wagon + matches 都不重复）────────────────

def test_repeat_apply_idempotent_wagons_and_matches(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    # First
    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    # Second
    r2 = create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    assert r2.skipped_existing_count == 3
    assert r2.inserted_count == 0

    conn = sqlite3.connect(str(db_path))
    ws_cnt = conn.execute(
        "SELECT COUNT(*) FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,)
    ).fetchone()[0]
    match_cnt = conn.execute(
        "SELECT COUNT(*) FROM shipment_release_batch_matches WHERE release_batch_id=?",
        (BATCH_ID,),
    ).fetchone()[0]
    conn.close()
    assert ws_cnt == 3
    assert match_cnt == 3


# ── Test 9: sop_agent.db 的 shipment_release_batch_matches 有 wagon_shipment_id FK ──

def test_match_has_wagon_shipment_id(tmp_path: Path):
    db_path = tmp_path / "sop.db"
    _create_sop_db(db_path)

    create_wagon_shipments_from_candidates(
        release_batch_id=BATCH_ID,
        departure_candidate=_make_departure(3),
        shipment_query_result=_make_query_result(_make_candidates(3)),
        db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Get a match row and verify its wagon_shipment_id points to an existing wagon
    match_row = conn.execute(
        "SELECT wagon_shipment_id FROM shipment_release_batch_matches LIMIT 1"
    ).fetchone()
    assert match_row is not None
    ws_row = conn.execute(
        "SELECT id FROM wagon_shipments WHERE id=?", (match_row["wagon_shipment_id"],)
    ).fetchone()
    assert ws_row is not None, "wagon_shipment_id in match does not point to existing wagon"
    conn.close()
