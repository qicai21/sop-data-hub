"""端到端 fixture:朝阳 中联发 发车 —— 复现并守住 2026-06-23 事故(#issue-20260623)。

事故:中联发发车 50 节,发车文本触发器**抓了 6 天前 matched 的 44 车旧候选**
(数量/车号全错)+ 'matched' 候选被反复抓 → 重传鞍钢 3 次。

本 e2e 把"发车文本 → rendezvous 三道闸 → 95306 合成 → 列号"串起来跑真函数,
焊死三个修复(commit e30a067 / 03aa252):
  ① 发车文本触发器正确抽取(船/到站/车数/项目)
  ② 三道闸:旧候选(已matched + 车数对不上 + 陈年)不被抓 → 走 95306 合成
  ③ 合成出的车 == 95306 权威 50 车
  ④ 列号按"实际发车趟次"(制票日去重)算,不按候选数

风格对齐 test_e2e_jilin_lanqi_50cars(fixture 双库 + 逐步骤跑真函数 + 外部mock)。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

L1 = "zlf_lot01"  # 中联发 lot01 batch id


def _rail_db(p: Path, n: int = 50, dest: str = "朝阳西", ts: str = "2026-06-23 03:45:0") -> str:
    """fixture 95306:n 车中联发(高桥镇→朝阳西,tyrjzsx 含中联发),复用 synthesize 表结构。"""
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE shipments (car_no TEXT, ticketed_at TEXT, cargo_name TEXT, "
        "destination_name TEXT, raw_core_json TEXT)"
    )
    rows = [
        (f"15{i:05d}", f"{ts}{i % 10}", "铁矿粉", dest,
         json.dumps({"tyrjzsx": "DL000757118 中联发 澳BHP麦克粉"}, ensure_ascii=False))
        for i in range(n)
    ]
    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(p)


def _sop_db(p: Path) -> str:
    conn = sqlite3.connect(str(p))
    conn.executescript(
        """
        CREATE TABLE inspection_ingestion_candidates (
            id TEXT PRIMARY KEY, source_file_name TEXT, status TEXT, reason TEXT,
            group_name TEXT, message_id TEXT, project_id TEXT, ship_name TEXT,
            destination TEXT, cargo_name TEXT, candidate_status TEXT, wagon_count INTEGER,
            car_numbers_json TEXT, payload_json TEXT, release_batch_id TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE workflow_task_db (id INTEGER PRIMARY KEY, created_at TEXT);
        CREATE TABLE wagon_shipments (
            id TEXT PRIMARY KEY, batch_id TEXT, car_no TEXT, ydid TEXT, ticketed_at TEXT);
        """
    )
    # 6 天前 matched 的 44 车旧候选(事故里被错抓的那条)
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, message_id, project_id, ship_name, destination, candidate_status, "
        " wagon_count, created_at) VALUES "
        "('stale44','wx_old','chaoyang_steel','中联发','朝阳西','matched',44,"
        " datetime('now','-6 days'))"
    )
    # 触发任务:created_at 设 4h 前 → 过合成宽限期(3h)
    conn.execute("INSERT INTO workflow_task_db (id, created_at) VALUES (1, datetime('now','-4 hours'))")
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture()
def cy_dbs(tmp_path, monkeypatch):
    rail = _rail_db(tmp_path / "95306.db")
    sop = _sop_db(tmp_path / "sop.db")
    # chain 内 RAIL_DB 与 synth 都走模块常量 → 注入 fixture
    monkeypatch.setattr("sop_hub.sop.workflow_task_executor._RAIL_DB_PATH", rail)
    return {"sop": sop, "rail": rail}


# ── ① 发车文本触发器抽取 ──────────────────────────────────────────────
def test_text_trigger_extracts_zhonglianfa():
    from sop_hub.sop.text_router import extract_inspection_text_triggers
    trigs = extract_inspection_text_triggers("十四道朝阳西铁  中联发 实装50节")
    zlf = [t for t in trigs if t.get("ship") == "中联发"]
    assert zlf, f"应抽到中联发触发器,得到 {trigs}"
    t = zlf[0]
    assert t["project_id"] == "chaoyang_steel"
    assert t["destination"] in ("朝阳西", "朝阳铁")
    assert t["expected_count"] == 50


# ── ②③ 三道闸:旧候选不被抓 → 走 95306 合成出正确 50 车 ──────────────
def test_stale_candidate_excluded_then_synthesize_correct_50(cy_dbs, monkeypatch):
    import sop_hub.sop.workflow_task_executor as wte

    # chain 换成哨兵:只记录"被指派的候选 id",不跑重活
    captured = {}

    def _fake_chain(input_json, message_id, *, db_path, task_id=0,
                    candidate_id=None, skip_upload=False):
        captured["candidate_id"] = candidate_id
        captured["skip_upload"] = skip_upload
        return {"action": "executed", "status": "succeeded", "output_json": {}}

    monkeypatch.setattr(wte, "_execute_chaoyang_inspection_chain", _fake_chain)
    monkeypatch.setattr("sop_hub.sop.send_excel.send_to_wechat",
                        lambda **kw: type("R", (), {"success": True, "error": ""})())

    res = wte._execute_inspection_text_trigger(
        {
            "trigger_project": "chaoyang_steel", "trigger_ship": "中联发",
            "trigger_dest": "朝阳西", "trigger_expected_count": 50,
            "message_inbox_id": 999, "received_datetime": "2026-06-23 03:19:00",
            "group_name": "铁晟业务工作群",
        },
        "wx_trig", db_path=Path(cy_dbs["sop"]), task_id=1,
    )

    # 没抓旧候选:指派给 chain 的不是 stale44
    assert captured.get("candidate_id") not in (None, "stale44")
    synth_id = captured["candidate_id"]
    # candidate_source 标 95306_synthesized(走了合成路径,非旧通知单候选)
    assert (res.get("output_json", {}).get("text_trigger", {}).get("candidate_source")
            == "95306_synthesized")
    # 合成候选真存在、车数=50、状态 candidate
    conn = sqlite3.connect(cy_dbs["sop"])
    row = conn.execute(
        "SELECT candidate_status, wagon_count, car_numbers_json FROM "
        "inspection_ingestion_candidates WHERE id=?", (synth_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "candidate"
    assert row[1] == 50, f"合成应 50 车,得 {row[1]}"
    assert len(json.loads(row[2])) == 50
    # 旧 44 候选原样不动(没被复用/改写)
    conn = sqlite3.connect(cy_dbs["sop"])
    stale = conn.execute(
        "SELECT candidate_status, wagon_count FROM inspection_ingestion_candidates "
        "WHERE id='stale44'").fetchone()
    conn.close()
    assert stale == ("matched", 44)


# ── ③ 合成的车号 == 95306 权威 50 车 ─────────────────────────────────
def test_synthesized_cars_equal_95306(cy_dbs):
    from sop_hub.sop.inspection_95306_synthesize import synthesize_candidate_from_95306
    conn = sqlite3.connect(cy_dbs["sop"])
    res = synthesize_candidate_from_95306(
        conn, project_id="chaoyang_steel", ship="中联发", dest="朝阳西",
        expected=50, trigger_ts="2026-06-23 03:19:00", group_name="g",
        message_id="wx_trig2", rail_db_path=cy_dbs["rail"],
    )
    conn.commit()
    rail = sqlite3.connect(cy_dbs["rail"])
    real = {r[0] for r in rail.execute("SELECT car_no FROM shipments")}
    rail.close()
    conn.close()
    assert res["status"] == "ok"
    assert set(res["car_nos"]) == real and len(real) == 50


# ── ④ 列号 = 实际发车趟次(制票日去重),不是候选数 ───────────────────
def test_lie_number_counts_actual_trips(cy_dbs):
    """中联发 5 趟(5 个制票日)→ 第 5 列;即使只有 1 条候选也不会数成第 1 列。"""
    conn = sqlite3.connect(cy_dbs["sop"])
    for i, d in enumerate(["2026-06-12", "2026-06-14", "2026-06-16", "2026-06-19", "2026-06-23"]):
        for j in range(3):  # 每趟几辆,关键是 distinct 制票日
            conn.execute(
                "INSERT INTO wagon_shipments (id, batch_id, car_no, ydid, ticketed_at) "
                "VALUES (?,?,?,?,?)",
                (f"w{i}_{j}", L1, f"c{i}{j}", f"yd{i}{j}", f"{d} 03:4{j}:00"))
    conn.commit()
    nth = conn.execute(
        "SELECT COUNT(DISTINCT substr(ticketed_at,1,10)) FROM wagon_shipments WHERE batch_id=?",
        (L1,)).fetchone()[0]
    conn.close()
    assert nth == 5, f"今早应是第 5 列(5 个制票日),得 {nth}"
