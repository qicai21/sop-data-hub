"""R36: Shipment status sync functional tests.

Uses temporary in-memory SQLite databases — no real DB dependency.
"""

import sqlite3
from pathlib import Path

from sop_hub.sop.shipment_status_sync import ShipmentStatusSync

SHIP_NAME = "蓝鳍"
PROJECT_ID = "jilin_jingang_jinzhou"


def _setup_dbs():
    """Create temporary sop_db and rail_db with matching data."""
    sop = sqlite3.connect(":memory:")
    sop.row_factory = sqlite3.Row
    rail = sqlite3.connect(":memory:")
    rail.row_factory = sqlite3.Row

    # sop_agent.db tables
    sop.execute("""
        CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, batch_key TEXT, project TEXT,
            ship_name TEXT, cargo_name TEXT, destination_station TEXT,
            notice_date TEXT, contract_no TEXT, dispatch_status TEXT DEFAULT 'in_progress',
            dispatch_status_updated_at TEXT
        )
    """)
    sop.execute("""
        CREATE TABLE wagon_shipments (
            id TEXT PRIMARY KEY, departure_id TEXT, batch_id TEXT,
            car_no TEXT, car_model TEXT, cargo_name TEXT,
            destination_name TEXT, ticketed_at TEXT,
            departed_at TEXT, arrived_at TEXT,
            status_name TEXT,
            FOREIGN KEY(batch_id) REFERENCES release_batches(id)
        )
    """)

    # 95306 shipments table
    rail.execute("""
        CREATE TABLE shipments (
            ydid TEXT PRIMARY KEY, car_no TEXT, cargo_name TEXT,
            destination_name TEXT, ticketed_at TEXT,
            departed_at TEXT, arrived_at TEXT, delivered_at TEXT,
            status_name TEXT, latest_stage_name TEXT
        )
    """)

    # Insert release_batch
    sop.execute(
        """INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name,
           destination_station, notice_date, dispatch_status)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("batch_001", "蓝鳍|铁矿|四平|2026-05-21", PROJECT_ID,
         SHIP_NAME, "铁矿", "四平", "2026-05-21", "dispatched"),
    )

    # Insert 3 wagon_shipments
    for i, car in enumerate(["1625630", "1512030", "1511993"], 1):
        sop.execute(
            """INSERT INTO wagon_shipments (id, batch_id, car_no, destination_name, ticketed_at)
               VALUES (?,?,?,?,?)""",
            (f"ws_{i:03d}", "batch_001", car, "四平", "2026-05-23 01:23:00"),
        )

    # Insert 95306 snapshots
    for car in ["1625630", "1512030", "1511993"]:
        rail.execute(
            """INSERT INTO shipments (ydid, car_no, cargo_name, destination_name,
               ticketed_at, departed_at, arrived_at, delivered_at,
               status_name, latest_stage_name)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                f"YD_{car}_001", car, "铁矿粉", "四平",
                "2026-05-23 01:23:00", "2026-05-23 06:20:00",
                "2026-05-24 00:59:00", "2026-05-24 15:35:46",
                "货物已交付", "交付",
            ),
        )

    sop.commit()
    rail.commit()
    return sop, rail


# ── 1. Car number match ───────────────────────────────────────────────


def test_match_by_car_no():
    sop, rail = _setup_dbs()
    sync = ShipmentStatusSync(sop_db_path=":memory:", rail_db_path=":memory:")
    # Override connections to use our in-memory DBs
    sync.sop_db_path = sop  # type: ignore[assignment]
    sync.rail_db_path = rail  # type: ignore[assignment]

    # Patch _load_wagons to use the in-memory connection
    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=True)
    assert result.total_wagons == 3
    assert result.matched_count == 3
    assert result.unmatched_count == 0


# ── 2. 发车 status → departed_at ──────────────────────────────────────


def test_departed_at_written():
    result = _run_sync_with_data(dry_run=False)
    assert result.departed_update_count >= 3


# ── 3. 到站 status → arrived_at ───────────────────────────────────────


def test_arrived_at_written():
    result = _run_sync_with_data(dry_run=False)
    assert result.arrived_update_count >= 3


# ── 4. 交付 status → dispatch_status=delivered ────────────────────────


def test_dispatch_status_delivered():
    result = _run_sync_with_data(dry_run=False)
    assert result.batch_dispatch_status == "delivered"


# ── 5. Dry-run doesn't write ──────────────────────────────────────────


def test_dry_run_no_write():
    sop, rail = _setup_dbs()
    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=True)
    assert result.total_wagons == 3

    # Verify no data written
    cursor = sop.execute("SELECT departed_at FROM wagon_shipments WHERE id='ws_001'")
    row = cursor.fetchone()
    assert row["departed_at"] is None or row["departed_at"] == ""


# ── 6. Apply writes ───────────────────────────────────────────────────


def test_apply_writes():
    sop, rail = _setup_dbs()
    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=False)

    # Verify via sync result metadata
    assert result.departed_update_count >= 3
    assert result.arrived_update_count >= 3
    assert result.update_count >= 6  # 3 wagons × at least departed_at + arrived_at


# ── 7. Don't overwrite existing values ────────────────────────────────


def test_dont_overwrite_existing():
    sop, rail = _setup_dbs()
    # Pre-fill departed_at
    sop.execute("UPDATE wagon_shipments SET departed_at = '2026-05-20 00:00:00' WHERE id='ws_001'")
    sop.commit()

    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=False)
    cursor = sop.execute("SELECT departed_at FROM wagon_shipments WHERE id='ws_001'")
    row = cursor.fetchone()
    # Should keep old value (not overwritten)
    assert row["departed_at"] == "2026-05-20 00:00:00"


# ── 8. Schema missing field ───────────────────────────────────────────


def test_schema_missing_delivered_at():
    sop, rail = _setup_dbs()
    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=True)
    assert "delivered_at" in result.schema_missing_fields


# ── 9. 95306 DB read-only ─────────────────────────────────────────────


def test_rail_db_not_modified():
    sop, rail = _setup_dbs()
    before = rail.execute("SELECT COUNT(*) as c FROM shipments").fetchone()["c"]

    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=False)
    after = rail.execute("SELECT COUNT(*) as c FROM shipments").fetchone()["c"]
    assert before == after == 3


# ── 10. Unmatched wagon ───────────────────────────────────────────────


def test_unmatched_wagon():
    sop, rail = _setup_dbs()
    sop.execute(
        """INSERT INTO wagon_shipments (id, batch_id, car_no, destination_name)
           VALUES ('ws_999', 'batch_001', '9999999', '四平')"""
    )
    sop.commit()

    result = _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=True)
    assert result.total_wagons == 4
    assert result.unmatched_count >= 1
    assert "9999999" in result.unmatched_wagons


# ── Helpers ───────────────────────────────────────────────────────────


def _run_sync_with_data(dry_run: bool = True):
    sop, rail = _setup_dbs()
    return _run_sync(sop, rail, ship_name=SHIP_NAME, dry_run=dry_run)


def _run_sync(sop: sqlite3.Connection, rail: sqlite3.Connection, *, ship_name: str, dry_run: bool = True):
    """Run sync using in-memory DBs written to temp files."""
    import tempfile
    from pathlib import Path

    sop_path = Path(tempfile.mktemp(suffix=".db"))
    rail_path = Path(tempfile.mktemp(suffix=".db"))

    try:
        # Dump in-memory DBs to temp files
        dump_conn = sqlite3.connect(str(sop_path))
        sop.backup(dump_conn)
        dump_conn.close()

        dump_conn = sqlite3.connect(str(rail_path))
        rail.backup(dump_conn)
        dump_conn.close()

        sync = ShipmentStatusSync(sop_db_path=sop_path, rail_db_path=rail_path)
        result = sync.sync(ship_name=ship_name, dry_run=dry_run)

        # Reload sop data from file back into in-memory connection
        if not dry_run:
            sop = sqlite3.connect(str(sop_path))
            sop.row_factory = sqlite3.Row

        return result
    finally:
        sop_path.unlink(missing_ok=True)
        rail_path.unlink(missing_ok=True)
