"""#issue-20260619:检装车链候选 ship/dest/project 推不出来时,
**挂起 pending_review + skipped**,而不是硬 failed 把候选/任务搞崩。

背景:一张"现场工作记录日报"被 VLM 误判成检装车通知单,infer 三层推不出
ship/dest/project → 候选已正确挂 pending_review。但链执行器旧逻辑仍硬返回
failed(error: candidate missing fields),污染 workflow_task 状态、inbox 显示
task_failed,看着像系统崩了。修复:优雅 skipped,候选保留待人工/后续 infer。
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
        " id TEXT PRIMARY KEY, message_id TEXT, ship_name TEXT, destination TEXT,"
        " cargo_name TEXT, project_id TEXT, candidate_status TEXT, reason TEXT,"
        " payload_json TEXT, extraction_json_path TEXT, wagon_count INTEGER,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO message_inbox (id, message_id, group_name, received_datetime, "
        " is_sop_msg, processing_status) "
        "VALUES (184,'wx_1263','铁晟业务工作群','2026-06-19 07:46:00',1,'matched_sop')"
    )
    # 日报误判 → infer 推不出字段 → 候选 ship/dest/project 全空(已 pending_review)
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, message_id, ship_name, destination, cargo_name, project_id, "
        " candidate_status, wagon_count, created_at) "
        "VALUES ('candEmpty','wx_1263','','','','','pending_review',3,'2026-06-19 07:46:01')"
    )
    conn.commit()
    conn.close()
    return p


def _run(p, candidate_id="candEmpty"):
    return wte._execute_chaoyang_inspection_chain(
        {"message_inbox_id": 184}, "wx_1263",
        db_path=p, task_id=None, candidate_id=candidate_id,
    )


def test_missing_fields_suspends_not_fails(db):
    res = _run(db)
    # 关键:不再 failed
    assert res["status"] == "skipped"
    assert res["action"] != "failed"
    assert res["output_json"]["candidate_status"] == "pending_review"
    assert res["output_json"]["reason"] == "candidate_missing_ship_dest_project"


def test_candidate_preserved_after_suspend(db):
    _run(db)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT candidate_status, reason FROM inspection_ingestion_candidates "
        "WHERE id='candEmpty'"
    ).fetchone()
    conn.close()
    # 候选没丢、仍是 pending_review、带上原因(人工可接管)
    assert row is not None
    assert row[0] == "pending_review"
    assert row[1] == "candidate_missing_ship_dest_project"
