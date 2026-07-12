"""#issue-20260619 Fix C:无检装车通知单时,从 95306 反查权威车合成候选。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sop_hub.sop.inspection_95306_synthesize import (
    supersede_synth_candidates_for_real_notice,
    synthesize_candidate_from_95306,
)


def _rail_db(tmp_path, cars):
    """cars: list of (car_no, ticketed_at, cargo_name, dest, tyrjzsx)。"""
    p = tmp_path / "rail.sqlite3"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE shipments (car_no TEXT, ticketed_at TEXT, cargo_name TEXT, "
        "destination_name TEXT, raw_core_json TEXT)"
    )
    conn.executemany(
        "INSERT INTO shipments VALUES (?,?,?,?,?)",
        [(c[0], c[1], c[2], c[3], json.dumps({"tyrjzsx": c[4]}, ensure_ascii=False))
         for c in cars],
    )
    conn.commit()
    conn.close()
    return str(p)


def _sop_db(tmp_path):
    p = tmp_path / "sop.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, source_file_name TEXT, status TEXT, reason TEXT,"
        " group_name TEXT, message_id TEXT, project_id TEXT, ship_name TEXT,"
        " destination TEXT, cargo_name TEXT, candidate_status TEXT, wagon_count INTEGER,"
        " car_numbers_json TEXT, payload_json TEXT, release_batch_id TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.commit()
    return conn


def test_synthesize_ok(tmp_path):
    # 中联发→朝阳西,触发 03:11,3 车制票 04:04(窗口内)
    cars = [(f"160000{i}", "2026-06-19 04:04:0%d" % i, "铁矿粉", "朝阳西",
             "DL000757118 中联发 澳BHP麦克粉") for i in range(3)]
    rail = _rail_db(tmp_path, cars)
    conn = _sop_db(tmp_path)
    res = synthesize_candidate_from_95306(
        conn, project_id="chaoyang_steel", ship="中联发", dest="朝阳西",
        expected=3, trigger_ts="2026-06-19 03:11:00", group_name="铁晟业务工作群",
        message_id="wx_1250", rail_db_path=rail,
    )
    assert res["status"] == "ok"
    assert len(res["car_nos"]) == 3
    row = conn.execute(
        "SELECT ship_name, destination, project_id, candidate_status, wagon_count, "
        "cargo_name, payload_json FROM inspection_ingestion_candidates WHERE id=?",
        (res["candidate_id"],),
    ).fetchone()
    assert row[0] == "中联发" and row[1] == "朝阳西" and row[2] == "chaoyang_steel"
    assert row[3] == "candidate" and row[4] == 3 and row[5] == "铁矿粉"
    payload = json.loads(row[6])
    assert payload["source"] == "95306_synthesized"
    assert [r["car_no"] for r in payload["rows"]] == res["car_nos"]  # 真车号入 rows


def test_synthesize_no_cars(tmp_path):
    # 窗口内没车(95306 还没制票)
    rail = _rail_db(tmp_path, [])
    conn = _sop_db(tmp_path)
    res = synthesize_candidate_from_95306(
        conn, project_id="chaoyang_steel", ship="中联发", dest="朝阳西",
        expected=44, trigger_ts="2026-06-19 03:11:00", group_name="g",
        message_id="wx_1", rail_db_path=rail,
    )
    assert res["status"] == "no_cars"
    assert conn.execute("SELECT COUNT(*) FROM inspection_ingestion_candidates").fetchone()[0] == 0


def test_synthesize_count_mismatch(tmp_path):
    # 95306 只 2 车,但预期 44 → 差太多,不合成
    cars = [(f"170000{i}", "2026-06-19 04:0%d:00" % i, "铁矿粉", "朝阳西",
             "DL x 中联发 y") for i in range(2)]
    rail = _rail_db(tmp_path, cars)
    conn = _sop_db(tmp_path)
    res = synthesize_candidate_from_95306(
        conn, project_id="chaoyang_steel", ship="中联发", dest="朝阳西",
        expected=44, trigger_ts="2026-06-19 03:11:00", group_name="g",
        message_id="wx_1", rail_db_path=rail,
    )
    assert res["status"] == "count_mismatch"
    assert conn.execute("SELECT COUNT(*) FROM inspection_ingestion_candidates").fetchone()[0] == 0


def test_synthesize_filters_other_ship(tmp_path):
    # 窗口内还有别船(亚历山大→凌源东),tyrjzsx 锚只取中联发
    cars = [
        ("1600001", "2026-06-19 04:04:01", "铁矿粉", "朝阳西", "DL 中联发 麦克粉"),
        ("1600002", "2026-06-19 04:04:02", "铁矿粉", "朝阳西", "DL 中联发 麦克粉"),
        ("9900001", "2026-06-19 04:05:00", "铁矿粉", "凌源东", "DL 亚历山大 粉"),
    ]
    rail = _rail_db(tmp_path, cars)
    conn = _sop_db(tmp_path)
    res = synthesize_candidate_from_95306(
        conn, project_id="chaoyang_steel", ship="中联发", dest="朝阳西",
        expected=2, trigger_ts="2026-06-19 03:11:00", group_name="g",
        message_id="wx_1", rail_db_path=rail,
    )
    assert res["status"] == "ok" and len(res["car_nos"]) == 2
    assert "9900001" not in res["car_nos"]


def test_real_notice_supersedes_matching_synth_candidate(tmp_path):
    conn = _sop_db(tmp_path)
    conn.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, group_name, message_id, project_id,
          ship_name, destination, cargo_name, candidate_status, wagon_count,
          car_numbers_json, payload_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "synth-1", "95306_synth:中联发", "candidate",
            "synthesized_from_95306_no_inspection_notice", "铁晟业务工作群",
            "wx_text_1", "chaoyang_steel", "中联发", "朝阳西", "铁矿粉",
            "pending_review", 3, '["1600000","1600001","1600002"]',
            json.dumps({"source": "95306_synthesized"}, ensure_ascii=False),
            "2026-06-19 03:20:00", "2026-06-19 03:20:00",
        ),
    )
    conn.commit()

    superseded = supersede_synth_candidates_for_real_notice(
        conn,
        survivor_candidate_id="real-1",
        project_id="chaoyang_steel",
        ship="中联发",
        dest="朝阳西",
        wagon_count=3,
        event_time="2026-06-19 04:30:00",
    )

    row = conn.execute(
        "SELECT candidate_status, reason FROM inspection_ingestion_candidates WHERE id='synth-1'"
    ).fetchone()
    assert superseded == ["synth-1"]
    assert row[0] == "superseded"
    assert row[1] == "superseded_by_real_inspection_notice"
