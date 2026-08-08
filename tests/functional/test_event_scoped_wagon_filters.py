from __future__ import annotations

import sqlite3

from sop_hub.external.chaoyang_ansteel.upload_wagons import fetch_wagons
from sop_hub.sop.departure_excel import _extract_rows
from sop_hub.sop.executor_runner import (
    _fetch_event_boxes_for_batch,
    _fetch_event_wagon_ids,
)
from sop_hub.sop.workflow_task_executor import _count_batch_rows_for_event


def _seed_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE release_batches (
          id TEXT PRIMARY KEY,
          project TEXT,
          ship_name TEXT,
          cargo_name TEXT,
          cargo_product_name TEXT,
          destination_station TEXT,
          contract_no TEXT,
          order_identifier TEXT
        );
        CREATE TABLE wagon_shipments (
          id TEXT,
          batch_id TEXT,
          car_no TEXT,
          ydid TEXT,
          ticketed_at TEXT,
          marked_weight REAL,
          car_model TEXT,
          origin_name TEXT,
          destination_name TEXT,
          container_numbers_json TEXT,
          container_no TEXT,
          source_message_id TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?)",
        ("batch1", "chaoyang_steel", "马兰幸福", "铁矿粉", "麦克粉", "朝阳西", "C1", "O1"),
    )
    rows = [
        ("old-1", "batch1", "1644106", "YD-old-1", "2026-07-05 18:26:31", 70, "C70", "高桥镇", "朝阳西", None, None, "wx_old"),
        ("old-2", "batch1", "1664992", "YD-old-2", "2026-07-05 18:26:35", 70, "C70", "高桥镇", "朝阳西", None, None, "wx_old"),
        ("new-1", "batch1", "1644106", "YD-new-1", "2026-07-08 10:51:07", 70, "C70", "高桥镇", "朝阳西", None, None, "wx_new"),
        ("new-2", "batch1", "1664992", "YD-new-2", "2026-07-08 10:51:15", 70, "C70", "高桥镇", "朝阳西", None, None, "wx_new"),
    ]
    conn.executemany("INSERT INTO wagon_shipments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_departure_excel_extract_rows_uses_ydids_to_avoid_reused_car_duplicates(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    rows, ctx, err = _extract_rows(
        "batch1",
        db_path=db,
        car_nos=["1644106", "1664992"],
        ydids=["YD-new-1", "YD-new-2"],
    )

    assert err == ""
    assert ctx["ship_name"] == "马兰幸福"
    assert [r["wagon_no"] for r in rows] == ["1644106", "1664992"]
    assert [r["ticketed_at_raw"] for r in rows] == [
        "2026-07-08 10:51:07",
        "2026-07-08 10:51:15",
    ]


def test_fetch_wagons_uses_ydids_to_avoid_reused_car_duplicates(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    wagons = fetch_wagons(
        db_path=db,
        batch_id="batch1",
        car_nos=["1644106", "1664992"],
        ydids=["YD-new-1", "YD-new-2"],
    )

    assert [w.car_no for w in wagons] == ["1644106", "1664992"]
    assert [w.waybill_time for w in wagons] == ["20260708105107", "20260708105115"]


def test_fetch_wagons_car_no_only_keeps_latest_ticket_per_car(tmp_path):
    """无 ydid 时不得把同车号旧趟一并捞出。"""
    db = tmp_path / "sop.db"
    _seed_db(db)

    wagons = fetch_wagons(
        db_path=db,
        batch_id="batch1",
        car_nos=["1644106", "1664992"],
        # ydids omitted on purpose
    )

    assert len(wagons) == 2
    assert [w.car_no for w in wagons] == ["1644106", "1664992"]
    assert [w.waybill_time for w in wagons] == ["20260708105107", "20260708105115"]


def test_departure_excel_car_no_only_keeps_latest_ticket_per_car(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    rows, ctx, err = _extract_rows(
        "batch1",
        db_path=db,
        car_nos=["1644106", "1664992"],
    )
    assert err == ""
    assert len(rows) == 2
    assert [r["ticketed_at_raw"] for r in rows] == [
        "2026-07-08 10:51:07",
        "2026-07-08 10:51:15",
    ]


def test_executor_runner_fetch_event_wagon_ids_prefers_ydids(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        ids = _fetch_event_wagon_ids(
            conn,
            "batch1",
            ydids=["YD-new-1", "YD-new-2"],
            car_nos=["1644106", "1664992"],
        )
    finally:
        conn.close()

    assert len(ids) == 2


def test_executor_runner_fetch_event_wagon_ids_prefers_source_message_id(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        ids = _fetch_event_wagon_ids(
            conn,
            "batch1",
            source_message_id="wx_new",
            ydids=["YD-old-1", "YD-old-2", "YD-new-1", "YD-new-2"],
            car_nos=["1644106", "1664992"],
        )
    finally:
        conn.close()

    assert set(ids) == {"new-1", "new-2"}


def test_executor_runner_fetch_event_boxes_prefers_ydids(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE wagon_container_shipments (
          batch_id TEXT,
          car_no TEXT,
          box_no TEXT,
          ydid TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO wagon_container_shipments VALUES (?,?,?,?)",
        [
            ("batch1", "1644106", "OLD-BOX-1", "YD-old-1"),
            ("batch1", "1664992", "OLD-BOX-2", "YD-old-2"),
            ("batch1", "1644106", "NEW-BOX-1", "YD-new-1"),
            ("batch1", "1664992", "NEW-BOX-2", "YD-new-2"),
        ],
    )
    conn.commit()
    try:
        boxes, keys = _fetch_event_boxes_for_batch(
            conn,
            "batch1",
            ydids=["YD-new-1", "YD-new-2"],
            car_nos=["1644106", "1664992"],
        )
    finally:
        conn.close()

    assert boxes == {"NEW-BOX-1", "NEW-BOX-2"}
    assert keys == {"NEW-BOX-1|1644106", "NEW-BOX-2|1664992"}


def test_workflow_event_count_rejects_car_only_fallback_for_wagon_projects(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        count = _count_batch_rows_for_event(
            conn,
            batch_id="batch1",
            project_id="zhongtang_special_steel",
            ydids=[],
            car_nos=["1644106", "1664992"],
        )
    finally:
        conn.close()

    assert count == 0


def test_workflow_event_count_prefers_ydids_for_wagon_projects(tmp_path):
    db = tmp_path / "sop.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        count = _count_batch_rows_for_event(
            conn,
            batch_id="batch1",
            project_id="chaoyang_steel",
            ydids=["YD-new-1", "YD-new-2"],
            car_nos=["1644106", "1664992"],
        )
    finally:
        conn.close()

    assert count == 2
