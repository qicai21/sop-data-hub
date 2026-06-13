"""#145:一 inbox 多船候选 → 每船各跑各的(不再 LIMIT 1 只跑首船)。

根因:_execute_chaoyang_inspection_chain 老行为对 candidate LIMIT 1,wx_753 4 船图
只跑首船,宝腾海当时靠手工补。修复:_multi 包装层按 inbox 全部 status='candidate'
候选,逐个 candidate_id 定向跑核心链。
"""
from __future__ import annotations

import sqlite3

import pytest

from sop_hub.sop.message_inbox import ensure_message_inbox_schema
import sop_hub.sop.workflow_task_executor as wte


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "sop.db"
    ensure_message_inbox_schema(db_path=p)
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, message_id TEXT, ship_name TEXT,"
        " destination TEXT, candidate_status TEXT, created_at TEXT)"
    )
    # 一条图片 inbox,拆出 2 船候选(均 status='candidate')
    conn.execute(
        "INSERT INTO message_inbox (id, message_id, group_name, received_datetime, "
        " is_sop_msg, processing_status) "
        "VALUES (300,'wx_img','铁晟业务工作群','2026-06-13 09:00:00',1,'matched_sop')"
    )
    conn.executemany(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, message_id, ship_name, destination, candidate_status, created_at) VALUES (?,?,?,?,?,?)",
        [
            ("candA", "wx_img", "鞍子河", "汐子", "candidate", "2026-06-13 09:00:01"),
            ("candB", "wx_img", "宝腾海", "朝阳西", "candidate", "2026-06-13 09:00:02"),
            # 已 matched 的不应再跑
            ("candC", "wx_img", "丰收散运", "汐子", "matched", "2026-06-13 09:00:03"),
        ],
    )
    conn.commit()
    conn.close()
    return p


def test_multi_runs_each_candidate(db, monkeypatch):
    calls = []

    def fake_core(input_json, message_id, *, db_path, task_id=0, candidate_id=None):
        calls.append(candidate_id)
        return {"action": "executed", "status": "succeeded",
                "output_json": {"stage": "done", "candidate_id": candidate_id}}

    monkeypatch.setattr(wte, "_execute_chaoyang_inspection_chain", fake_core)

    res = wte._execute_chaoyang_inspection_chain_multi(
        {"message_inbox_id": 300}, "wx_img", db_path=db,
    )
    assert res["status"] == "succeeded"
    assert res["output_json"]["stage"] == "multi_candidate_fanout"
    assert res["output_json"]["candidate_count"] == 2
    # 两个 status='candidate' 各定向跑一次,matched 的 candC 跳过
    assert sorted(calls) == ["candA", "candB"]


def test_multi_single_candidate_degrades_to_core(db, monkeypatch):
    # 删到只剩 1 个 candidate → 退化为直接调核心(不传 candidate_id,老语义)
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM inspection_ingestion_candidates WHERE id IN ('candB','candC')")
    conn.commit()
    conn.close()

    seen = {}

    def fake_core(input_json, message_id, *, db_path, task_id=0, candidate_id=None):
        seen["candidate_id"] = candidate_id
        return {"action": "executed", "status": "succeeded", "output_json": {}}

    monkeypatch.setattr(wte, "_execute_chaoyang_inspection_chain", fake_core)
    res = wte._execute_chaoyang_inspection_chain_multi(
        {"message_inbox_id": 300}, "wx_img", db_path=db,
    )
    assert res["status"] == "succeeded"
    # 退化路径不传 candidate_id(沿用核心自己 LIMIT 1)
    assert seen["candidate_id"] is None


def test_multi_surfaces_failure(db, monkeypatch):
    def fake_core(input_json, message_id, *, db_path, task_id=0, candidate_id=None):
        if candidate_id == "candB":
            return {"action": "failed", "status": "failed",
                    "error_message": "boom"}
        return {"action": "executed", "status": "succeeded", "output_json": {}}

    monkeypatch.setattr(wte, "_execute_chaoyang_inspection_chain", fake_core)
    res = wte._execute_chaoyang_inspection_chain_multi(
        {"message_inbox_id": 300}, "wx_img", db_path=db,
    )
    # 任一候选 failed → 整体 failed,但每候选结果都在 per_candidate 里
    assert res["status"] == "failed"
    ships = {pc["ship"]: pc["status"] for pc in res["output_json"]["per_candidate"]}
    assert ships == {"鞍子河": "succeeded", "宝腾海": "failed"}
