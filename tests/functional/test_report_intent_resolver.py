"""Functional tests for the report-intent resolver.

Scope:
- local WorkflowTask -> ReportIntent mapping only
- no runtime, wx-ops-agent, database, delivery, or report sending
"""

from sop_hub.sop.report_intent import resolve_report_intent
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
    cases = [
        (
            "zhongtang_special_steel",
            "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/ztsteel_departure_report_template.xlsx",
        ),
        (
            "chaoyang_steel",
            "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/cysteel_departure_report_template.xlsx",
        ),
        (
            "jilin_jingang_jinzhou",
            "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/jilin_jingang_departure_report_template.xlsx",
        ),
    ]

    for project_id, template_path_suffix in cases:
        intent = resolve_report_intent(_task(project_id))

        assert intent.status == "ready"
        assert intent.report_type == "departure_report"
        assert intent.template_path is not None
        assert intent.template_path.endswith(template_path_suffix)
        # 2026-06-06 #95:收件人改 yaml-driven,从 flows.report_delivery_flow.send_report.target_group 解析
        # 当前 yaml 全部配 ["GROUP013"] 数据单发群 → type='group' / name='[GROUP013]'
        assert intent.recipient_target == {"type": "group", "name": "[GROUP013]"}
        assert intent.required_fields == ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"]
        assert intent.missing_fields == []
        assert intent.project_id == project_id
        assert intent.message_id == "msg-001"
        assert intent.group_id == "GROUP001"
        assert intent.target_sop_node == "node-1"


def test_resolve_report_intent_marks_missing_fields_explicitly():
    task = _task("chaoyang_steel", target_sop_node="")
    task = WorkflowTask(
        task_id=task.task_id,
        message_id=task.message_id,
        group_id=task.group_id,
        project_id=task.project_id,
        target_sop_node=task.target_sop_node,
        watch_item={},
        status=task.status,
        reason=task.reason,
    )

    intent = resolve_report_intent(task)

    assert intent.status == "incomplete"
    assert intent.report_type == "departure_report"
    assert intent.template_path is not None
    assert intent.template_path.endswith("cysteel_departure_report_template.xlsx")
    # 2026-06-06 #95:同上,yaml-driven
    assert intent.recipient_target == {"type": "group", "name": "[GROUP013]"}
    assert "target_sop_node" in intent.missing_fields
    assert "watch_item" in intent.missing_fields
    assert intent.project_id == "chaoyang_steel"
    assert intent.required_fields == ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"]
