import sqlite3

import pytest

from sop_hub.sop.dispatch_plan import allocate_container_ydids


def test_container_reallocation_refreshes_dashboard_weight(tmp_path):
    """A box-level reassignment must refresh the release-batch dashboard facts."""
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE release_batches (
          id TEXT PRIMARY KEY, project TEXT, batch_quantity REAL,
          total_planned_quantity REAL, shipped_weight_tons REAL,
          remaining_weight_tons REAL, unresolved_wagon_count INTEGER,
          actual_wagon_count INTEGER, shipped_weight_last_computed_at TEXT,
          updated_at TEXT, transport_mode TEXT
        );
        CREATE TABLE wagon_shipments (id TEXT PRIMARY KEY, batch_id TEXT, container_batch_map TEXT);
        CREATE TABLE wagon_container_shipments (
          id TEXT PRIMARY KEY, batch_id TEXT, ydid TEXT, box_no TEXT,
          car_no TEXT, project_id TEXT, ship_name TEXT, ticketed_at TEXT, box_position INTEGER,
          updated_at TEXT
        );
        CREATE TABLE release_batch_dispatch_plan (
          release_batch_id TEXT PRIMARY KEY, project_id TEXT, ship_name TEXT,
          planned_box_count INTEGER, allocated_box_count INTEGER,
          priority_order INTEGER, status TEXT, updated_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO release_batches (id, project, batch_quantity, shipped_weight_tons) VALUES (?,?,?,?)",
        [("old", "jilin_jingang_jinzhou", 5000, 64.56), ("lot11", "jilin_jingang_jinzhou", 5000, 0)],
    )
    conn.execute(
        "INSERT INTO release_batch_dispatch_plan VALUES ('lot11','jilin_jingang_jinzhou','蓝鳍',154,0,1,'active',NULL)"
    )
    conn.executemany(
        "INSERT INTO wagon_container_shipments VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (f"box-{i}", "old", f"Y-{i // 2}", f"TBJU{i:07d}", str(1000000 + i // 2),
                "jilin_jingang_jinzhou", "蓝鳍", "2026-09-01 10:00:00", i % 2 + 1, None)
            for i in range(154)
        ],
    )
    conn.commit()
    conn.close()

    result = allocate_container_ydids(
        [f"Y-{i}" for i in range(77)], "jilin_jingang_jinzhou", "蓝鳍",
        db_path=db, force_overwrite=True,
    )

    assert not result.error
    assert result.recompute_errors == {}
    assert set(result.recomputed_batch_ids) == {"old", "lot11"}
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT shipped_weight_tons, remaining_weight_tons, actual_wagon_count FROM release_batches WHERE id='lot11'"
    ).fetchone()
    assert row == pytest.approx((154 * 32.28, 5000 - 154 * 32.28, 77))
