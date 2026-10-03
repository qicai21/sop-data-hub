"""Regression coverage for audited late/forwarded inspection images."""
from __future__ import annotations

import json
import sqlite3

from sop_hub.sop.workflow_task_executor import _resolve_inspection_event_time


def _db(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message_inbox (id INTEGER PRIMARY KEY, received_datetime TEXT)"
    )
    conn.execute(
        "INSERT INTO message_inbox VALUES (1, '2026-10-03 06:40:48')"
    )
    conn.commit()
    return conn


def test_uses_received_time_without_manual_event_override(tmp_path):
    conn = _db(tmp_path)
    try:
        when, source = _resolve_inspection_event_time(
            conn, inbox_id=1, candidate={"payload_json": "{}"}
        )
    finally:
        conn.close()
    assert when == "2026-10-03 06:40:48"
    assert source == "inbox_received_time"


def test_uses_audited_original_time_for_late_forwarded_notice(tmp_path):
    conn = _db(tmp_path)
    payload = {
        "_manual_event_time": {
            "event_time": "2026-10-02 17:18:54",
            "operator_note": "中唐特钢发运群原始文字消息已核对",
        }
    }
    try:
        when, source = _resolve_inspection_event_time(
            conn, inbox_id=1, candidate={"payload_json": json.dumps(payload)}
        )
    finally:
        conn.close()
    assert when == "2026-10-02 17:18:54"
    assert source == "manual_confirmed_event_time"


def test_rejects_unexplained_or_malformed_manual_time(tmp_path):
    conn = _db(tmp_path)
    payload = {"_manual_event_time": {"event_time": "not-a-time", "operator_note": "x"}}
    try:
        when, source = _resolve_inspection_event_time(
            conn, inbox_id=1, candidate={"payload_json": json.dumps(payload)}
        )
    finally:
        conn.close()
    assert when == "2026-10-03 06:40:48"
    assert source == "inbox_received_time"
