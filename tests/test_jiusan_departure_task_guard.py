"""九三内部群发车文本任务的来源守门。"""
from __future__ import annotations

from sop_hub.sop.text_watch_daemon import AUTO_SAFE_TASK_TYPES
from sop_hub.sop.workflow_task_store import is_valid_jiusan_internal_departure_row


def _row(group_name: str, text: str) -> dict:
    return {
        "group_name": group_name,
        "text_content": text,
        "message_id": "wx_test",
        "received_datetime": "2026-07-13 02:13:06",
    }


def test_only_internal_group_complete_jiusan_departure_text_is_accepted():
    assert is_valid_jiusan_internal_departure_row(
        _row("铁晟大豆业务内部沟通群", "七道 新台子 大豆 美国 50节")
    )
    assert not is_valid_jiusan_internal_departure_row(
        _row("数据单发群", "七道 新台子 大豆 美国 50节")
    )
    assert not is_valid_jiusan_internal_departure_row(
        _row("铁晟大豆业务内部沟通群", "第36位6921013改为6921015")
    )


def test_jiusan_departure_text_reconcile_is_an_auto_safe_task():
    assert "jiusan_departure_text_reconcile" in AUTO_SAFE_TASK_TYPES
