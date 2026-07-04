from __future__ import annotations

import sqlite3
from pathlib import Path

from sop_hub.sop.jiusan_departure_reconcile import reconcile_jiusan_departure_text


SHIPMENT_COLS = (
    "ydid", "czydid", "car_no", "car_model", "cargo_name", "shipper_name",
    "consignee_name", "origin_name", "destination_name", "accepted_at", "loaded_at",
    "ticketed_at", "departed_at", "arrived_at", "delivered_at", "status_name",
    "latest_stage_key", "latest_stage_name", "latest_event_time", "marked_weight",
    "freight_fee", "transport_mode_code", "transport_mode_name",
)


def _make_sop_db(tmp_path: Path) -> Path:
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY,
            project TEXT,
            ship_name TEXT,
            batch_sequence TEXT,
            dispatch_status TEXT,
            dispatch_status_note TEXT,
            dispatch_status_updated_at TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE bulk_loading_notice_wagon (
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
        )"""
    )
    conn.executemany(
        "INSERT INTO release_batches "
        "(id, project, ship_name, batch_sequence, dispatch_status) VALUES (?, 'jiusan', ?, 'lot02', 'loading')",
        [("B_CX", "诚信"), ("B_US", "美国")],
    )
    conn.commit()
    conn.close()
    return db


def _make_rail_db(tmp_path: Path, n: int) -> Path:
    db = tmp_path / "rail.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shipments ("
        + ",".join(f"{col} TEXT" for col in SHIPMENT_COLS)
        + ")"
    )
    rows = []
    for i in range(1, n + 1):
        row = {col: "" for col in SHIPMENT_COLS}
        row.update(
            {
                "ydid": f"YD{i:03d}",
                "car_no": f"8100{i:03d}",
                "car_model": "L18",
                "cargo_name": "大豆",
                "origin_name": "高桥镇",
                "destination_name": "新台子",
                "transport_mode_name": "整车运输",
                "ticketed_at": f"2026-07-03 02:{i:02d}:00",
                "status_name": "已发车",
                "marked_weight": "60",
            }
        )
        rows.append(tuple(row[col] for col in SHIPMENT_COLS))
    conn.executemany(
        "INSERT INTO shipments VALUES (" + ",".join("?" for _ in SHIPMENT_COLS) + ")",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def test_single_ship_departure_text_inserts_bulk_notice_rows(tmp_path):
    sop_db = _make_sop_db(tmp_path)
    rail_db = _make_rail_db(tmp_path, 2)
    result = reconcile_jiusan_departure_text(
        {
            "message_id": "wx_test_1",
            "group_name": "铁晟大豆业务内部沟通群",
            "received_datetime": "2026-07-03 06:00:00",
            "text_content": "八道 2节 新台子 大豆 诚信",
        },
        db_path=sop_db,
        rail_db=rail_db,
        run_sync=False,
    )
    assert result["status"] == "succeeded"
    assert result["output_json"]["notice_rows_inserted"] == 2

    conn = sqlite3.connect(sop_db)
    rows = conn.execute(
        "SELECT ship_name, track, COUNT(*) FROM bulk_loading_notice_wagon GROUP BY ship_name, track"
    ).fetchall()
    conn.close()
    assert rows == [("诚信", "八道", 2)]


def test_mixed_ship_text_requires_manual_car_level_split(tmp_path):
    sop_db = _make_sop_db(tmp_path)
    rail_db = _make_rail_db(tmp_path, 50)
    result = reconcile_jiusan_departure_text(
        {
            "message_id": "wx_test_2",
            "group_name": "铁晟大豆业务内部沟通群",
            "received_datetime": "2026-07-03 06:00:00",
            "text_content": "七道 新台子 大豆 诚信 5节，45节 美国 K车",
        },
        db_path=sop_db,
        rail_db=rail_db,
        run_sync=False,
    )
    assert result["status"] == "skipped"
    assert result["action"] == "manual_review_required"
    assert "mixed ships require car-level split" in result["output_json"]["reason"]

    conn = sqlite3.connect(sop_db)
    count = conn.execute("SELECT COUNT(*) FROM bulk_loading_notice_wagon").fetchone()[0]
    conn.close()
    assert count == 0

