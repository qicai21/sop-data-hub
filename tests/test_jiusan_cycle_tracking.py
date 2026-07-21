from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from sop_hub.sop.jiusan_cycle_tracking import (
    TRANSFERRED_CYCLES,
    classify_train_state,
    select_four_probes,
    update_cycle_tracking_cache,
)


NOW = datetime(2026, 7, 21, 12, 0, 0)


def test_transferred_cycle_register_includes_four_and_five():
    assert TRANSFERRED_CYCLES == frozenset({4, 5})


def _sample(**overrides):
    row = {
        "ydid": "y1",
        "car_no": "1000001",
        "ticketed_at": "2026-07-21 01:00:00",
        "departed_at": "",
        "arrived_at": "",
        "unloading_inbound": "",
        "unloading_outbound": "",
    }
    row.update(overrides)
    return row


def test_select_four_probes_covers_both_historical_halves():
    cars = [
        {"ydid": f"y{i:02d}", "car_no": f"{i:07d}", "ticketed_at": f"2026-07-21 01:{i:02d}:00"}
        for i in range(1, 11)
    ]
    history = {
        row["car_no"]: ("2026-07-20 08:00:00" if i <= 5 else "2026-07-20 10:00:00")
        for i, row in enumerate(cars, start=1)
    }
    probes, strategy = select_four_probes(cars, history)

    assert strategy == "previous_outbound_halves"
    assert [p["ydid"] for p in probes] == ["y01", "y05", "y06", "y10"]
    assert [p["probe_role"] for p in probes] == [
        "half_a_first", "half_a_last", "half_b_first", "half_b_last",
    ]


def test_select_four_probes_falls_back_to_train_edges_and_half_boundary():
    cars = [
        {"ydid": f"y{i}", "car_no": str(i), "ticketed_at": f"2026-07-21 01:{i:02d}:00"}
        for i in range(8)
    ]
    probes, strategy = select_four_probes(cars, {})
    assert strategy == "quartiles"
    assert [p["ydid"] for p in probes] == ["y0", "y3", "y4", "y7"]


def test_cycle_state_uses_first_inbound_and_last_outbound_plus_ten_hours():
    ticketed = [_sample(ydid=f"y{i}") for i in range(4)]
    assert classify_train_state(ticketed, now=NOW)["node_label"] == "港内制票/待发"

    transit = [dict(s, departed_at="2026-07-21 02:00:00") for s in ticketed]
    assert classify_train_state(transit, now=NOW)["node_label"] == "在途(重)"

    waiting = [dict(s, arrived_at="2026-07-21 08:00:00") for s in transit]
    assert classify_train_state(waiting, now=NOW)["node_label"] == "新台子到站等待"

    working = [dict(s) for s in waiting]
    working[0]["unloading_inbound"] = "2026-07-21 09:00:00"
    assert classify_train_state(working, now=NOW)["node_label"] == "三三零线上作业"

    outbound = [
        dict(s, unloading_inbound="2026-07-21 09:00:00", unloading_outbound="2026-07-21 10:00:00")
        for s in waiting
    ]
    outbound[-1]["unloading_outbound"] = "2026-07-21 11:00:00"
    empty = classify_train_state(outbound, now=NOW)
    assert empty["node_label"] == "在途(空)"
    assert empty["next_transition_at"] == "2026-07-21 21:00:00"

    returned = classify_train_state(outbound, now=NOW + timedelta(hours=10))
    assert returned["node_label"] == "返港待装"


def test_update_cache_queries_exactly_four_per_active_cycle(tmp_path):
    sop = tmp_path / "sop.db"
    rail = tmp_path / "rail.db"
    cache = tmp_path / "cache.json"

    conn = sqlite3.connect(sop)
    conn.executescript(
        """
        CREATE TABLE wagon_body_pool(project TEXT, car_no TEXT, home_cycle_no INTEGER);
        CREATE TABLE wagon_container_shipments(
          project_id TEXT, ship_name TEXT, ydid TEXT, car_no TEXT,
          ticketed_at TEXT, departed_at TEXT
        );
        """
    )
    for i in range(1, 9):
        car = f"10000{i:02d}"
        conn.execute("INSERT INTO wagon_body_pool VALUES('jiusan',?,1)", (car,))
        conn.execute(
            "INSERT INTO wagon_container_shipments VALUES('jiusan','勇气',?,?,?,?)",
            (f"old{i}", car, f"2026-07-19 01:{i:02d}:00", "2026-07-19 06:00:00"),
        )
        conn.execute(
            "INSERT INTO wagon_container_shipments VALUES('jiusan','勇气',?,?,?,?)",
            (f"new{i}", car, f"2026-07-21 01:{i:02d}:00", "2026-07-21 06:00:00"),
        )
    conn.commit()
    conn.close()

    rconn = sqlite3.connect(rail)
    rconn.execute("CREATE TABLE shipments(ydid TEXT PRIMARY KEY, loading_unloading_timeline_json TEXT)")
    for i in range(1, 9):
        outbound = "2026-07-20 08:00:00" if i <= 4 else "2026-07-20 10:00:00"
        rconn.execute(
            "INSERT INTO shipments VALUES(?,?)",
            (f"old{i}", json.dumps({"xcdcsj": outbound})),
        )
    rconn.commit()
    rconn.close()

    calls = []

    def fake_query(row):
        calls.append(row["ydid"])
        return {
            "ticketed_at": row["ticketed_at"],
            "departed_at": row["departed_at"],
            "arrived_at": "",
            "unloading_inbound": "",
            "unloading_outbound": "",
        }

    report = update_cycle_tracking_cache(
        sop_db=sop,
        rail_db=rail,
        cache_path=cache,
        query_one=fake_query,
        now=NOW,
        sleep_seconds=0,
    )

    assert len(calls) == 4
    assert report["query_count"] == 4
    assert report["error_count"] == 0
    assert report["trains"][0]["sample_strategy"] == "previous_outbound_halves"
    assert report["trains"][0]["node_label"] == "在途(重)"
    assert json.loads(cache.read_text(encoding="utf-8"))["query_count"] == 4
