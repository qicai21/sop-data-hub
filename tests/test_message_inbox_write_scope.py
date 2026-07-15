"""Regression coverage for message_id collisions across WeChat groups."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sop_hub.sop.message_inbox import ensure_message_inbox_schema
from sop_hub.sop.text_router import TextRouteResult, update_message_inbox_with_route
from sop_hub.sop.workflow_task_executor import _update_message_inbox_status


def _prepare_db(path: Path) -> None:
    ensure_message_inbox_schema(path)
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO message_inbox (id, group_id, message_id, processing_status) VALUES (?, ?, ?, ?)",
        [(1, "group-a", "wx_2026-07_1", "new"), (2, "group-b", "wx_2026-07_1", "new")],
    )
    conn.commit()
    conn.close()


def test_router_writeback_is_scoped_to_group_and_message_id(tmp_path: Path) -> None:
    db = tmp_path / "inbox.db"
    _prepare_db(db)
    route = TextRouteResult(
        message_id="wx_2026-07_1", group_id="group-a", is_sop_msg=True,
        sop_project_id="jiusan", processing_status="matched_sop",
    )
    assert update_message_inbox_with_route(route.message_id, route, db_path=str(db))
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT group_id, processing_status, sop_project_id FROM message_inbox ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [("group-a", "matched_sop", "jiusan"), ("group-b", "new", None)]


def test_task_status_writeback_requires_message_inbox_primary_key(tmp_path: Path) -> None:
    db = tmp_path / "inbox.db"
    _prepare_db(db)
    assert not _update_message_inbox_status(db, "wx_2026-07_1", "task_succeeded")
    assert _update_message_inbox_status(db, "wx_2026-07_1", "task_succeeded", 1)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT processing_status FROM message_inbox ORDER BY id").fetchall()
    conn.close()
    assert rows == [("task_succeeded",), ("new",)]
