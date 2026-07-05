from __future__ import annotations

import sqlite3

from sop_hub.sop.dispatch_train_code import assign_dispatch_train_codes


def _make_db(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE wagon_shipments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            batch_id TEXT,
            source_message_id TEXT,
            source_group_id TEXT,
            loading_line TEXT,
            destination_name TEXT,
            ticketed_at TEXT,
            departed_at TEXT,
            loaded_at TEXT,
            accepted_at TEXT,
            created_at TEXT,
            ship_name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE wagon_container_shipments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            batch_id TEXT,
            source_message_id TEXT,
            source_group_id TEXT,
            loading_line TEXT,
            destination_name TEXT,
            ticketed_at TEXT,
            departed_at TEXT,
            loaded_at TEXT,
            accepted_at TEXT,
            created_at TEXT,
            ship_name TEXT,
            car_no TEXT,
            box_no TEXT,
            ydid TEXT
        )
        """
    )
    conn.commit()
    return db, conn


def test_same_source_across_batches_gets_one_train_code(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.executemany(
        """
        INSERT INTO wagon_shipments (
            id, project_id, batch_id, source_message_id, ticketed_at, ship_name
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("w1", "zhongtang_special_steel", "B1", "wx_2026-06_62", "2026-06-27 13:21:04", "丰收散运"),
            ("w2", "zhongtang_special_steel", "B1", "wx_2026-06_62", "2026-06-27 13:22:04", "丰收散运"),
            ("w3", "zhongtang_special_steel", "B2", "wx_2026-06_62", "2026-06-27 13:25:38", "贝拉"),
            ("w4", "zhongtang_special_steel", "B3", "wx_2026-06_71", "2026-06-28 04:45:44", "丰收散运"),
        ],
    )

    result = assign_dispatch_train_codes(conn)

    assert result["updated"] == 4
    rows = conn.execute(
        "SELECT id, dispatch_train_code FROM wagon_shipments ORDER BY id"
    ).fetchall()
    codes = {row[0]: row[1] for row in rows}
    assert codes["w1"] == codes["w2"] == codes["w3"]
    assert codes["w1"] == "gqz2606271"
    assert codes["w4"] == "gqz2606281"


def test_same_source_is_shared_between_wagon_and_container_tables(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.execute(
        """
        INSERT INTO wagon_shipments (
            id, project_id, batch_id, source_message_id, ticketed_at
        ) VALUES ('w1', 'jilin_jingang_jinzhou', 'B1', 'wx_2026-07_1', '2026-07-04 10:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO wagon_container_shipments (
            id, project_id, batch_id, source_message_id, ticketed_at, car_no, box_no, ydid
        ) VALUES ('c1', 'jilin_jingang_jinzhou', 'B1', 'wx_2026-07_1', '2026-07-04 10:00:00', '8100001', 'TBJU1', 'Y1')
        """
    )

    assign_dispatch_train_codes(conn)

    wagon_code = conn.execute(
        "SELECT dispatch_train_code FROM wagon_shipments WHERE id='w1'"
    ).fetchone()[0]
    container_code = conn.execute(
        "SELECT dispatch_train_code FROM wagon_container_shipments WHERE id='c1'"
    ).fetchone()[0]
    assert wagon_code == container_code == "jg2607041"


def test_existing_code_is_preserved_and_propagated(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.execute("ALTER TABLE wagon_shipments ADD COLUMN dispatch_train_code TEXT")
    conn.executemany(
        """
        INSERT INTO wagon_shipments (
            id, project_id, batch_id, source_message_id, ticketed_at, dispatch_train_code
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("w1", "chaoyang_steel", "B1", "wx_a", "2026-07-01 06:00:00", "cg2607013"),
            ("w2", "chaoyang_steel", "B2", "wx_a", "2026-07-01 06:05:00", ""),
        ],
    )

    assign_dispatch_train_codes(conn)

    codes = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, dispatch_train_code FROM wagon_shipments")
    }
    assert codes == {"w1": "cg2607013", "w2": "cg2607013"}
