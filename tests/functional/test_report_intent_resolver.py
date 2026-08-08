"""Functional tests for the deprecated report-intent resolver.

Scope:
- local WorkflowTask -> ReportIntent mapping only
- no runtime, wx-ops-agent, database, delivery, or report sending
- no absolute machine paths; template_path is None (departure_excel is production)
"""

from __future__ import annotations

import warnings

from sop_hub.sop.report_intent import ReportIntent, resolve_report_intent
from sop_hub.sop.workflow_task import WorkflowTask


def _task(project_id: str, target_sop_node: str = "node-1") -> WorkflowTask:
    return WorkflowTask(
        task_id=f"task-{project_id or 'unknown'}",
        message_id="msg-001",
        group_id="GROUP001",
        project_id=project_id,
        target_sop_node=target_sop_node,
        watch_item={"document_type": "出港计划通知单"},
        status="planned",
        reason="matched group_id GROUP001 and watch item",
    )


def test_resolve_report_intent_for_ordinary_freight_projects():
    for project_id in (
        "zhongtang_special_steel",
        "chaoyang_steel",
        "jilin_jingang_jinzhou",
    ):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            intent = resolve_report_intent(_task(project_id))

        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
        assert intent.status == "ready"
        assert intent.report_type == "departure_report"
        # Production path is departure_excel; retired templates must not reappear
        # as absolute /Users/... paths or phantom on-disk xlsx claims.
        assert intent.template_path is None
        assert intent.recipient_target == {"type": "group", "name": "[GROUP013]"}
        assert intent.required_fields == [
            "message_id",
            "group_id",
            "project_id",
            "target_sop_node",
            "watch_item",
        ]
        assert intent.missing_fields == []
        assert intent.project_id == project_id
        assert intent.message_id == "msg-001"
        assert intent.group_id == "GROUP001"
        assert intent.target_sop_node == "node-1"


def test_resolve_report_intent_marks_missing_fields_explicitly():
    task = WorkflowTask(
        task_id="task-chaoyang_steel",
        message_id="msg-001",
        group_id="GROUP001",
        project_id="chaoyang_steel",
        target_sop_node="",
        watch_item={},
        status="planned",
        reason="matched",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        intent = resolve_report_intent(task)

    assert intent.status == "incomplete"
    assert intent.report_type == "departure_report"
    assert intent.template_path is None
    assert intent.recipient_target == {"type": "group", "name": "[GROUP013]"}
    assert "target_sop_node" in intent.missing_fields
    assert "watch_item" in intent.missing_fields
    assert intent.project_id == "chaoyang_steel"


def test_report_intent_can_be_constructed_without_templates():
    """Simulations/tests should build ReportIntent explicitly when needed."""
    intent = ReportIntent(
        template_path=None,
        recipient_target={"type": "group", "name": "[GROUP013]"},
        required_fields=["message_id"],
        missing_fields=[],
        report_type="departure_report",
        status="ready",
        message_id="m1",
        group_id="g1",
        project_id="chaoyang_steel",
        target_sop_node="n1",
    )
    assert intent.template_path is None
    assert intent.status == "ready"
