import sqlite3

from sop_hub.sop.dispatch_plan import set_plan


def test_set_plan_seeds_allocated_count_from_existing_container_rows(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE release_batch_dispatch_plan (
          release_batch_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          ship_name TEXT NOT NULL,
          planned_box_count INTEGER NOT NULL,
          allocated_box_count INTEGER DEFAULT 0,
          priority_order INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE wagon_container_shipments (
          id TEXT PRIMARY KEY,
          batch_id TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO wagon_container_shipments (id, batch_id) VALUES (?, ?)",
        [(f"box-{i}", "lot02") for i in range(150)],
    )
    conn.commit()
    conn.close()

    rows = set_plan(
        "jilin_jingang_jinzhou",
        "马兰希望",
        [("lot02", 306, 2), ("lot03", 306, 3)],
        db_path=db,
    )

    by_id = {row.release_batch_id: row for row in rows}
    assert by_id["lot02"].allocated_box_count == 150
    assert by_id["lot02"].status == "active"
    assert by_id["lot03"].allocated_box_count == 0

    conn = sqlite3.connect(db)
    stored = dict(
        conn.execute(
            "SELECT release_batch_id, allocated_box_count "
            "FROM release_batch_dispatch_plan"
        ).fetchall()
    )
    conn.close()
    assert stored == {"lot02": 150, "lot03": 0}


def test_set_plan_marks_completed_when_existing_boxes_reach_plan(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE release_batch_dispatch_plan (
          release_batch_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          ship_name TEXT NOT NULL,
          planned_box_count INTEGER NOT NULL,
          allocated_box_count INTEGER DEFAULT 0,
          priority_order INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          notes TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE wagon_container_shipments (
          id TEXT PRIMARY KEY,
          batch_id TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO wagon_container_shipments (id, batch_id) VALUES (?, ?)",
        [(f"box-{i}", "lot02") for i in range(306)],
    )
    conn.commit()
    conn.close()

    rows = set_plan(
        "jilin_jingang_jinzhou",
        "马兰希望",
        [("lot02", 306, 2)],
        db_path=db,
    )

    assert rows[0].allocated_box_count == 306
    assert rows[0].status == "completed"
