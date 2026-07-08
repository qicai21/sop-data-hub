from __future__ import annotations

import sqlite3

from sop_hub.external.chaoyang_ansteel.upload_wagons import fetch_wagons
from sop_hub.sop.departure_excel import _extract_rows


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
          batch_id TEXT,
          car_no TEXT,
          ydid TEXT,
          ticketed_at TEXT,
          marked_weight REAL,
          car_model TEXT,
          origin_name TEXT,
          destination_name TEXT,
          container_numbers_json TEXT,
          container_no TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?)",
        ("batch1", "chaoyang_steel", "马兰幸福", "铁矿粉", "麦克粉", "朝阳西", "C1", "O1"),
    )
    rows = [
        ("batch1", "1644106", "YD-old-1", "2026-07-05 18:26:31", 70, "C70", "高桥镇", "朝阳西", None, None),
        ("batch1", "1664992", "YD-old-2", "2026-07-05 18:26:35", 70, "C70", "高桥镇", "朝阳西", None, None),
        ("batch1", "1644106", "YD-new-1", "2026-07-08 10:51:07", 70, "C70", "高桥镇", "朝阳西", None, None),
        ("batch1", "1664992", "YD-new-2", "2026-07-08 10:51:15", 70, "C70", "高桥镇", "朝阳西", None, None),
    ]
    conn.executemany("INSERT INTO wagon_shipments VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
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
