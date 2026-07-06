"""九三循环列看板:途重 parse + 返空状态机口径测试。

工单:docs/issues/2026-06-29-工单-循环列港重途重返空.md
覆盖:
  ① jiusan_morning_report_ingest.parse_report 在途重箱解析("在途N节重箱"/"…集装箱")。
  ② jiusan_cycle_board._cycle_state 返空口径(用户铁律 §返空 规则1/2/3 + 总池跨船 + 求和)。
运行:PYTHONPATH=src python3 -m pytest tests/test_jiusan_cycle_board.py -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import jiusan_cycle_board as jcb  # noqa: E402
import jiusan_morning_report_ingest as ing  # noqa: E402

NOW = datetime(2026, 6, 29, 13, 0)


# ── ① 途重 parse ──────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expect", [
    ("【目前在途情况】\n1.在途40节重箱，预计晚间到达\n", 80),   # 6/29 实测措辞
    ("在途50节集装箱,4点迁出港内", 100),                        # 6/12 历史措辞
    ("1.在途无\n2.新台子", 0),                                  # 无在途
    ("1.在途50节散粮车，预计上午达到。", 0),                     # 散粮非集装箱,不计
])
def test_parse_transit_loaded(text, expect):
    assert ing.parse_report(text)["transit_loaded"] == expect


# ── ② 返空状态机 ──────────────────────────────────────────────────────
def _mkdb(trips: dict, ships: dict | None = None):
    """trips: {home_cycle_no: (dep, arr, total_boxes, delivered_boxes, last_deliv)}。
    ships: 可选 {home_cycle_no: ship_name},缺省 's'(用于阶段锚测试需指定锚船)。"""
    ships = ships or {}
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE wagon_body_pool(car_no TEXT,project TEXT,home_cycle_no INT)")
    c.execute("CREATE TABLE wagon_container_shipments("
              "car_no TEXT,box_no TEXT,batch_id TEXT,ship_name TEXT,"
              "departed_at TEXT,arrived_at TEXT,delivered_at TEXT)")
    car = 0
    for cyc, (dep, arr, total, deliv, last_deliv) in trips.items():
        ship = ships.get(cyc, "s")
        for i in range(total):
            car += 1
            cn = f"c{car}"
            c.execute("INSERT INTO wagon_body_pool VALUES(?,?,?)", (cn, "jiusan", cyc))
            c.execute("INSERT INTO wagon_container_shipments VALUES(?,?,?,?,?,?,?)",
                      (cn, f"b{car}", "X", ship, dep, arr, last_deliv if i < deliv else ""))
    c.commit()
    return c


def _state(trips):
    return jcb._cycle_state(_mkdb(trips), now=NOW)


def test_rule1_full_delivered_over_5h_returns_home():
    # 唯一列全交付,末交付 7h 前(>5h)→ 返空回港,返空=0,该列=在装(港重)
    st = _state({1: ("2026-06-28 10:00:00", "2026-06-28 14:00:00", 100, 100, "2026-06-29 06:00:00")})
    assert st["transit_empty_boxes"] == 0
    assert st["pos"]["port_loaded"]["cyc"] == 1


def test_rule1_full_delivered_under_5h_in_transit():
    # 全交付仅 2h(<5h)且无更新列 → 返空在途=100
    st = _state({1: ("2026-06-29 03:00:00", "2026-06-29 07:00:00", 100, 100, "2026-06-29 11:00:00")})
    assert st["transit_empty_boxes"] == 100
    assert st["transit_empty_cycs"] == [1]


def test_rule2_older_under_5h_superseded_returns_home():
    # 旧列<5h,但有更新进站列已开卸 → 旧列也算回港(规则2);返空=0,旧列=在装
    st = _state({
        1: ("2026-06-29 03:00:00", "2026-06-29 07:00:00", 100, 100, "2026-06-29 11:00:00"),
        2: ("2026-06-29 05:00:00", "2026-06-29 09:00:00", 100, 30, "2026-06-29 12:00:00"),
    })
    assert st["transit_empty_boxes"] == 0
    assert st["pos"]["port_loaded"]["cyc"] == 1


def test_rule3_partial_with_newer_prompts_confirm():
    # 旧列到站仅部分交付(30/100)且有更新进站列开卸 → 不计返空 + 人工确认
    st = _state({
        1: ("2026-06-27 10:00:00", "2026-06-27 14:00:00", 100, 30, "2026-06-28 02:00:00"),
        2: ("2026-06-28 10:00:00", "2026-06-28 14:00:00", 100, 50, "2026-06-29 06:00:00"),
    })
    assert st["transit_empty_boxes"] == 0
    assert len(st["confirms"]) == 1
    assert "需人工确认" in st["confirms"][0]


def test_transit_loaded_departed_not_arrived():
    # 已发车未到站 → 途重,不计返空
    st = _state({1: ("2026-06-29 10:00:00", "", 100, 0, "")})
    assert st["transit_empty_boxes"] == 0
    assert st["pos"]["transit_loaded"]["cyc"] == 1


def test_phase_anchor_excludes_pre_anchor_ships():
    # 阶段锚(工单 §3b):锚船=和谐1,总池跨船但不回溯锚船之前。
    # cyc1=和谐1(锚,6/10起);cyc2=锚前旧船(3月)→ 必须被排除,不进循环列状态。
    db = _mkdb(
        {1: ("2026-06-10 18:00:00", "2026-06-10 23:00:00", 100, 100, "2026-06-29 06:00:00"),
         2: ("2026-03-01 10:00:00", "2026-03-01 15:00:00", 50, 50, "2026-03-02 06:00:00")},
        ships={1: jcb.PHASE_START_SHIP, 2: "旧船3月"},
    )
    assert jcb._phase_start_date(db) == "2026-06-10 18:00:00"
    cycs = {t["cyc"] for t in jcb._col_latest_trips(db)}
    assert cycs == {1}            # 锚前的 cyc2 被阶段锚排除


def test_pool_key_is_project_level():
    # §3a:snapshot key 已项目级化,看板/ingest 共用同一项目级 key
    import jiusan_morning_report_ingest as ing2
    assert jcb.POOL_KEY == "九三大豆"
    assert ing2.POOL_KEY == jcb.POOL_KEY


def test_morning_report_header_day_typo_uses_message_date():
    date, warn = ing.resolve_snapshot_date("5", "2026-07-06")

    assert date == "2026-07-06"
    assert "表头写截止5日" in warn


def test_morning_report_matching_header_day_uses_cutoff_date():
    date, warn = ing.resolve_snapshot_date("6", "2026-07-06")

    assert date == "2026-07-06"
    assert warn == ""


def test_dashboard_cargo_label_prefers_specific_product_name():
    import cli_dashboard as dashboard

    assert dashboard._display_cargo_name({
        "cargo_name": "铁矿粉",
        "cargo_product_name": "混合粉",
    }) == "混合粉"


def test_concurrent_returns_summed_not_overwritten():
    # 两列同时到站(互不 supersede)且均<5h → 返空箱**求和**(根治旧版覆盖只剩末列的 bug)
    st = _state({
        1: ("2026-06-29 03:00:00", "2026-06-29 07:00:00", 100, 100, "2026-06-29 11:00:00"),
        2: ("2026-06-29 03:00:00", "2026-06-29 07:00:00", 88, 88, "2026-06-29 10:00:00"),
    })
    assert st["transit_empty_boxes"] == 188
    assert sorted(st["transit_empty_cycs"]) == [1, 2]
