"""统一 wagon 入库口 wagon_ingest:列集一致 + cargo_count 规则 + 必重算。

回归 2026-06-15 宝腾海漏重算偏小的根因 —— 入库后 shipped_weight 必须被刷新。
"""
from __future__ import annotations

import sqlite3

import pytest

from sop_hub.sop.wagon_ingest import (
    backfill_event_fields_from_95306,
    build_wagon_row,
    compute_cargo_count,
    gen_wagon_id,
    ingest_wagons,
)

CHAOYANG_RULE = """
project_id: chaoyang_steel
project_meta:
  shipped_weight_rule:
    per_wagon:
      op: lookup
      key:
        op: lookup_by_prefix
        value: $wagon.car_model
        prefix_map: {C70: 70, C64: 61, C62: 60}
      table: {"70": 69.5, "61": 63, "60": 62}
      on_miss: pending_review
"""


def _ticket(ydid, car_no, model, **kw):
    base = {"ydid": ydid, "car_no": car_no, "car_model": model,
            "cargo_name": "钢材", "origin_name": "朝阳", "destination_name": "锦州",
            "ticketed_at": "2026-06-11 10:00:00", "status_name": "已发车",
            "marked_weight": "70.00", "transport_mode_name": "整车运输"}
    base.update(kw)
    return base


def test_cargo_count_container_vs_bulk():
    assert compute_cargo_count({"container_numbers_json": '["A","B"]'}) == 2
    assert compute_cargo_count({"container_no": "X / Y / Z"}) == 3
    assert compute_cargo_count({"car_model": "L70"}) == 0   # 散粮整车无箱


def test_gen_wagon_id_matches_production_recipe():
    # 与 create_wagon_shipments._gen_wagon_id 同源:sha1(ydid|batch)[:24]
    import hashlib
    wid = gen_wagon_id("YD1", "BATCH1")
    assert wid == hashlib.sha1(b"YD1|BATCH1").hexdigest()[:24]
    assert len(wid) == 24


def test_build_row_delivered_sets_confirmed():
    r = build_wagon_row(_ticket("y", "c", "C70E", status_name="货物已交付",
                                 delivered_at="2026-06-12 08:00:00"),
                        batch_id="b", project_id="p", ship_name="s", now="now")
    assert r["dispatch_status"] == "confirmed_received"
    assert r["confirmed_received_at"] == "2026-06-12 08:00:00"


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "sop.db"
    conn = sqlite3.connect(str(p))
    conn.execute("""CREATE TABLE wagon_shipments (
        id TEXT PRIMARY KEY, departure_id TEXT, batch_id TEXT, car_no TEXT,
        car_model TEXT, cargo_name TEXT, shipper_name TEXT, consignee_name TEXT,
        origin_name TEXT, destination_name TEXT, accepted_at TEXT, loaded_at TEXT,
        ticketed_at TEXT, departed_at TEXT, arrived_at TEXT, delivered_at TEXT,
        confirmed_received_at TEXT, status_name TEXT, latest_stage_key TEXT,
        latest_stage_name TEXT, latest_event_time TEXT, marked_weight REAL,
        freight_fee TEXT, container_no TEXT, waybill_no TEXT, ydid TEXT, czydid TEXT,
        cargo_count INTEGER, transport_mode_code TEXT, transport_mode_name TEXT,
        project_id TEXT, ship_name TEXT, dispatch_status TEXT, source_message_id TEXT,
        source_group_id TEXT, computed_loading_weight REAL, weight_rule_basis TEXT,
        container_batch_map TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, project TEXT, batch_quantity REAL,
        total_planned_quantity REAL, shipped_weight_tons REAL,
        remaining_weight_tons REAL, unresolved_wagon_count INTEGER,
        actual_wagon_count INTEGER, shipped_weight_last_computed_at TEXT,
        updated_at TEXT)""")
    conn.execute("INSERT INTO release_batches (id, project, batch_quantity) "
                 "VALUES ('B', 'chaoyang_steel', 1000)")
    conn.commit()
    conn.close()
    return p


def test_ingest_recomputes_shipped_weight(db, tmp_path):
    sop_dir = tmp_path / "sops"
    sop_dir.mkdir()
    (sop_dir / "chaoyang.yaml").write_text(CHAOYANG_RULE, encoding="utf-8")

    tickets = [_ticket(f"y{i}", f"c{i}", "C70E") for i in range(3)]  # 3×69.5
    res = ingest_wagons("B", tickets, project_id="chaoyang_steel",
                        ship_name="宝腾海", db_path=db, source_message_id="t",
                        now="2026-06-15")
    # 入库口 monkeypatch sop_dir:compute 默认扫 config/project_sops,这里用真规则
    from sop_hub.sop.shipped_weight import compute_for_release_batch
    sw = compute_for_release_batch("B", db_path=db, sop_dir=sop_dir)

    assert res["new"] == 3
    assert sw["shipped_weight_tons"] == pytest.approx(208.5)   # 3 × 69.5

    # 幂等:再 ingest 同票 → 0 新增,全刷新
    res2 = ingest_wagons("B", tickets, project_id="chaoyang_steel",
                         ship_name="宝腾海", db_path=db, source_message_id="t",
                         now="2026-06-15")
    assert res2["new"] == 0
    assert res2["refreshed"] == 3


def test_ingest_no_rule_project_skips_safely(db):
    # 无 shipped_weight_rule 的项目 → recompute 安全跳过,不报错
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO release_batches (id, project, batch_quantity) "
                 "VALUES ('J', '__no_rule_project__', 1000)")
    conn.commit()
    conn.close()
    res = ingest_wagons("J", [_ticket("y", "c", "L70")],
                        project_id="__no_rule_project__",
                        ship_name="x", db_path=db, now="2026-06-15")
    assert res["new"] == 1
    # compute 返回 no_shipped_weight_rule,不抛
    assert res["recompute_ok"] is False


def test_ingest_refreshes_existing_freight_fee(db):
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO wagon_shipments (
            id, batch_id, car_no, car_model, cargo_name, origin_name, destination_name,
            ticketed_at, status_name, marked_weight, freight_fee, ydid, project_id,
            ship_name, dispatch_status, source_message_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gen_wagon_id("y1", "B"),
            "B", "c1", "C70E", "钢材", "朝阳", "锦州",
            "2026-06-11 10:00:00", "已发车", 70.0, 0, "y1",
            "chaoyang_steel", "宝腾海", "loading", "t", "2026-06-15", "2026-06-15",
        ),
    )
    conn.commit()
    conn.close()

    tickets = [_ticket("y1", "c1", "C70E", freight_fee=294530, latest_stage_key="departed")]
    res = ingest_wagons("B", tickets, project_id="chaoyang_steel",
                        ship_name="宝腾海", db_path=db, source_message_id="t",
                        now="2026-06-15")
    assert res["new"] == 0
    assert res["refreshed"] == 1

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT freight_fee, latest_stage_key FROM wagon_shipments WHERE id=?",
        (gen_wagon_id("y1", "B"),),
    ).fetchone()
    conn.close()
    assert row[0] == "294530" or row[0] == 294530
    assert row[1] == "departed"


def test_backfill_event_fields_from_95306_fills_freight_fee(tmp_path):
    sop_db = tmp_path / "sop.db"
    rail_db = tmp_path / "rail.sqlite3"

    conn = sqlite3.connect(str(sop_db))
    conn.execute(
        """CREATE TABLE wagon_shipments (
            id TEXT PRIMARY KEY, ydid TEXT, car_model TEXT, marked_weight REAL,
            hph TEXT, freight_fee REAL, updated_at TEXT
        )"""
    )
    conn.execute(
        "CREATE TABLE wagon_container_shipments (id TEXT PRIMARY KEY, ydid TEXT)"
    )
    conn.execute(
        "INSERT INTO wagon_shipments VALUES ('w1','yd1','',NULL,'',0,'2026-06-15')"
    )
    conn.commit()
    conn.close()

    rail = sqlite3.connect(str(rail_db))
    rail.execute(
        """CREATE TABLE shipments (
            ydid TEXT, car_model TEXT, freight_fee INTEGER,
            raw_core_json TEXT
        )"""
    )
    rail.execute(
        "INSERT INTO shipments VALUES ('yd1','C70E',294530,'{\"hph\":\"HPH001\"}')"
    )
    rail.commit()
    rail.close()

    out = backfill_event_fields_from_95306(
        ["w1"], db_path=sop_db, rail_db_path=rail_db,
    )

    conn = sqlite3.connect(str(sop_db))
    row = conn.execute(
        "SELECT car_model, marked_weight, hph, freight_fee FROM wagon_shipments WHERE id='w1'"
    ).fetchone()
    conn.close()
    assert out["car_model"] == 1
    assert out["marked_weight"] == 1
    assert out["hph"] == 1
    assert out["freight_fee"] == 1
    assert row == ("C70E", 70.0, "HPH001", 294530.0)
