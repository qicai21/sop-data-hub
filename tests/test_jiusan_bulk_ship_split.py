"""#issue-20260620-散粮sync按船拆分:sync_jiusan_bulk_wagons 多船路由守门。

守住:同列混船(诚信+和谐1)→ 按简装车通知单台账(ydid)各归各船 lot02;
错挂的票自愈重路由;未命中的已存在行 grandfather 保留;全新未命中告警。
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sync_jiusan_bulk_wagons", REPO / "scripts" / "sync_jiusan_bulk_wagons.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _ticket(ydid: str, car: str, model: str = "L18", status: str = "已发车") -> dict:
    base = {k: "" for k in (
        "czydid", "cargo_name", "shipper_name", "consignee_name",
        "origin_name", "destination_name", "accepted_at", "loaded_at",
        "departed_at", "arrived_at", "delivered_at", "latest_stage_key",
        "latest_stage_name", "latest_event_time", "freight_fee",
        "transport_mode_code", "transport_mode_name",
    )}
    base.update({
        "ydid": ydid, "car_no": car, "car_model": model,
        "ticketed_at": "2026-06-19T08:00:00+08:00",
        "status_name": status, "marked_weight": 61.0,
    })
    return base


def _make_db(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE release_batches (id TEXT, project TEXT, "
              "batch_sequence TEXT, ship_name TEXT, batch_count INTEGER, updated_at TEXT)")
    c.execute("INSERT INTO release_batches VALUES ('BH','jiusan','lot02','和谐1',0,'')")
    c.execute("INSERT INTO release_batches VALUES ('BC','jiusan','lot02','诚信',0,'')")
    cols = list(m.build_row(_ticket("seed", "seed"), "now", "和谐1", "BH").keys())
    c.execute(f"CREATE TABLE wagon_shipments ({','.join(f'{x} TEXT' for x in cols)})")
    c.commit()
    return c


def _insert(c: sqlite3.Connection, ticket: dict, ship: str, batch: str) -> None:
    r = m.build_row(ticket, "t0", ship, batch)
    cols = list(r.keys())
    c.execute(f"INSERT INTO wagon_shipments ({','.join(cols)}) "
              f"VALUES ({','.join('?' * len(cols))})", [r[x] for x in cols])


def test_mixed_ship_reroute_and_grandfather(tmp_path):
    c = _make_db(tmp_path)
    # 诚信票当前错挂和谐1;和谐1自己的票在和谐1。
    _insert(c, _ticket("YD_CX", "car_cx"), "和谐1", "BH")
    _insert(c, _ticket("YD_H1", "car_h1"), "和谐1", "BH")
    c.commit()

    ship_batches = {"和谐1": "BH", "诚信": "BC"}
    notice_map = {"YD_CX": "诚信"}  # 仅诚信入台账;和谐1历史票未命中
    tickets = [_ticket("YD_CX", "car_cx"), _ticket("YD_H1", "car_h1")]
    routed = m.resolve_routing(tickets, notice_map, ship_batches, "和谐1")
    stats = m.upsert_rows(c, routed, "t1", ship_batches)

    # 诚信票自愈重路由到诚信;和谐1未命中票 grandfather 保留。
    assert stats["reroute"] == 1
    assert stats["refresh"] == 1
    assert stats["new"] == 0
    cx = c.execute("SELECT batch_id, ship_name FROM wagon_shipments WHERE ydid='YD_CX'").fetchone()
    assert (cx["batch_id"], cx["ship_name"]) == ("BC", "诚信")
    h1 = c.execute("SELECT batch_id, ship_name FROM wagon_shipments WHERE ydid='YD_H1'").fetchone()
    assert (h1["batch_id"], h1["ship_name"]) == ("BH", "和谐1")


def test_new_unmatched_warns_and_falls_back(tmp_path):
    c = _make_db(tmp_path)
    ship_batches = {"和谐1": "BH", "诚信": "BC"}
    # 全新且未命中台账的散粮车 → 暂落兜底船(和谐1)+ 进 WARN 清单。
    routed = m.resolve_routing([_ticket("YD_NEW", "car_new")], {}, ship_batches, "和谐1")
    stats = m.upsert_rows(c, routed, "t1", ship_batches)
    assert stats["new"] == 1
    assert len(stats["warn_new_unmatched"]) == 1
    row = c.execute("SELECT batch_id, ship_name FROM wagon_shipments WHERE ydid='YD_NEW'").fetchone()
    assert (row["batch_id"], row["ship_name"]) == ("BH", "和谐1")


def test_matched_routes_directly_when_new(tmp_path):
    c = _make_db(tmp_path)
    ship_batches = {"和谐1": "BH", "诚信": "BC"}
    routed = m.resolve_routing([_ticket("YD_CX2", "car_cx2")], {"YD_CX2": "诚信"}, ship_batches, "和谐1")
    stats = m.upsert_rows(c, routed, "t1", ship_batches)
    assert stats["new"] == 1
    assert not stats["warn_new_unmatched"]
    row = c.execute("SELECT batch_id, ship_name FROM wagon_shipments WHERE ydid='YD_CX2'").fetchone()
    assert (row["batch_id"], row["ship_name"]) == ("BC", "诚信")


def test_completed_lots_still_accept_notice_mapped_history(tmp_path):
    c = _make_db(tmp_path)
    ship_batches = {"和谐1": "BH", "诚信": "BC"}
    # 全部 lot02 已完成时，已命中内部群台账的历史票仍可补入对应批次。
    routed = m.resolve_routing(
        [_ticket("YD_CX_HISTORY", "car_cx")], {"YD_CX_HISTORY": "诚信"}, ship_batches, None,
    )
    stats = m.upsert_rows(c, routed, "t1", ship_batches)
    assert stats["new"] == 1
    assert not stats["deferred_new_unmatched"]
    row = c.execute("SELECT batch_id, ship_name FROM wagon_shipments WHERE ydid='YD_CX_HISTORY'").fetchone()
    assert (row["batch_id"], row["ship_name"]) == ("BC", "诚信")


def test_completed_lots_defer_unknown_new_ticket(tmp_path):
    c = _make_db(tmp_path)
    ship_batches = {"和谐1": "BH", "诚信": "BC"}
    routed = m.resolve_routing([_ticket("YD_UNKNOWN", "car_unknown")], {}, ship_batches, None)
    stats = m.upsert_rows(c, routed, "t1", ship_batches)
    assert stats["new"] == 0
    assert stats["deferred_new_unmatched"] == [("YD_UNKNOWN", "car_unknown", "2026-06-19")]
    assert c.execute("SELECT 1 FROM wagon_shipments WHERE ydid='YD_UNKNOWN'").fetchone() is None


def test_resolve_fallback_ship_never_returns_finished_ship(tmp_path):
    c = _make_db(tmp_path)
    c.execute("ALTER TABLE release_batches ADD COLUMN dispatch_status TEXT")
    c.execute("ALTER TABLE release_batches ADD COLUMN notice_date TEXT")
    c.execute("ALTER TABLE release_batches ADD COLUMN created_at TEXT")
    c.execute("ALTER TABLE release_batches ADD COLUMN dispatch_status_updated_at TEXT")
    c.execute("UPDATE release_batches SET dispatch_status='confirmed_received', notice_date='2026-06-09', created_at='2026-06-09', updated_at='2026-07-08', dispatch_status_updated_at='2026-07-08' WHERE ship_name='和谐1'")
    c.execute("UPDATE release_batches SET dispatch_status='enriched', notice_date='2026-07-02', created_at='2026-07-02', updated_at='2026-07-08', dispatch_status_updated_at='2026-07-08' WHERE ship_name='诚信'")
    c.commit()
    assert m.resolve_fallback_ship(c) == "诚信"
