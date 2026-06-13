"""#144:95306 时间窗反推锚点选错(锚到同车旧票)回归测试。

车皮会反复发运:同 car_no + 同到站历史上有旧票。锚点查询不带时间下界时
ORDER BY ticketed_at DESC 会拿到旧批次的票,把整个时间窗锚错位。
修复:min_ticketed_at(= 通知时间 - 12h)以前的票视为"本批还没制票"。
"""
from __future__ import annotations

import sqlite3

import pytest

from sop_hub.sop.inspection_window_recover import recover_loading_cars_via_window


@pytest.fixture()
def rail_db(tmp_path):
    db = tmp_path / "rail.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE shipments ("
        " ydid TEXT PRIMARY KEY, car_no TEXT, destination_name TEXT,"
        " cargo_name TEXT, ticketed_at TEXT)"
    )
    rows = [
        # 旧批次:车 1000001/1000002 在 5/20 发过同到站同货
        ("old-1", "1000001", "朝阳西", "铁矿", "2026-05-20 10:00:00"),
        ("old-2", "1000002", "朝阳西", "铁矿", "2026-05-20 10:01:00"),
        # 本批次:6/11 19:00 窗口 3 车
        ("new-1", "1000001", "朝阳西", "铁矿", "2026-06-11 19:00:00"),
        ("new-2", "1000002", "朝阳西", "铁矿", "2026-06-11 19:02:00"),
        ("new-3", "1000003", "朝阳西", "铁矿", "2026-06-11 19:05:00"),
    ]
    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def test_anchor_skips_old_ticket_when_batch_not_ticketed(rail_db, tmp_path):
    """本批还没制票 + 锚点车有旧票 → 必须 no_ticket_yet,不能锚到旧票。"""
    conn = sqlite3.connect(str(rail_db))
    conn.execute("DELETE FROM shipments WHERE ydid LIKE 'new-%'")
    conn.commit()
    conn.close()

    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001", "1000002"],
        all_notice_car_nos=["1000001", "1000002", "1000003"],
        destination="朝阳西",
        min_ticketed_at="2026-06-11 06:00:00",
    )
    assert r["status"] == "no_ticket_yet"
    assert r["anchor_car_no"] is None


def test_anchor_picks_current_batch_ticket(rail_db):
    """新旧票都在,下界把旧票排除,窗内反推出本批 3 车。"""
    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001", "1000002"],
        all_notice_car_nos=["1000001", "1000002", "1000003"],
        destination="朝阳西",
        min_ticketed_at="2026-06-11 06:00:00",
    )
    assert r["status"] == "ok"
    assert r["anchor_ticketed_at"] == "2026-06-11 19:00:00"
    assert r["loading_car_nos"] == ["1000001", "1000002", "1000003"]


def test_legacy_no_bound_anchors_old_ticket(rail_db, tmp_path):
    """不带下界(老行为)会锚到旧票 — 文档化 #144 的 bug 形态。"""
    conn = sqlite3.connect(str(rail_db))
    conn.execute("DELETE FROM shipments WHERE ydid LIKE 'new-%'")
    conn.commit()
    conn.close()

    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001"],
        all_notice_car_nos=["1000001", "1000002"],
        destination="朝阳西",
    )
    # 老行为:锚到 5/20 旧票,窗内交出旧批 2 车 — 这就是 #144 要避免的错位
    assert r["anchor_ticketed_at"] == "2026-05-20 10:00:00"
