"""Functional tests for the local SOP lifecycle closeout chain.

Scope:
- MessageEvent -> RawAssetBundle -> monitoring plan match -> WorkflowTask ->
  ReportIntent -> DeliveryResult -> LifecycleCloseout
- no real sending, runtime, wx-ops-agent, DB, OCR execution, or 95306
"""

from ops_hub.sop.delivery_result import closeout_lifecycle
from ops_hub.sop.monitoring_plan_compiler import SopMonitoringPlanCompiler
from ops_hub.sop.monitoring_plan_matcher import MessageEvent, match_message_event
from ops_hub.sop.raw_asset_bundle import register_raw_asset_bundle
from ops_hub.sop.report_intent import resolve_report_intent
from ops_hub.sop.workflow_task import WorkflowTask, build_workflow_task_queue


def _project_sops() -> list[dict[str, object]]:
    return [
        {
            "project_id": "chaoyang_steel",
            "project_name": "朝阳钢铁",
            "sop_nodes": [
                {
                    "node_id": "departure_plan_notice",
                    "node_name": "出港计划通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "GROUP001",
                            "group_name": "铁晟业务工作群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        }
                    ],
                }
            ],
        }
    ]


def _full_chain_task() -> WorkflowTask:
    plan = SopMonitoringPlanCompiler().compile(_project_sops())
    bundle = register_raw_asset_bundle(
        message_id="msg-001",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T10:00:00+08:00",
        raw_image_path="/tmp/sop-data-hub/msg-001.jpg",
        ocr_json_path="/tmp/sop-data-hub/msg-001.ocr.json",
        message_metadata_path="/tmp/sop-data-hub/msg-001.meta.json",
        text="出港计划通知单",
        extraction_kind="image",
    )
    event = MessageEvent(
        message_id="msg-001",
        channel="wechat",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T10:00:00+08:00",
        message_type="image",
        text="",
        raw_asset_bundle=bundle,
    )
    match_result = match_message_event(event, plan)
    queue = build_workflow_task_queue(event, bundle, match_result)

    assert len(queue.workflow_tasks) == 1
    assert queue.todo_items == []
    return queue.workflow_tasks[0]


def test_full_local_lifecycle_chain_closes_successfully():
    task = _full_chain_task()
    intent = resolve_report_intent(task)
    closeout = closeout_lifecycle(task, intent, delivery_success=True, confirmation_ref="confirm-001")

    assert intent.status == "ready"
    assert intent.project_id == "chaoyang_steel"
    assert intent.report_type == "departure_report"
    assert closeout.delivery_result is not None
    assert closeout.delivery_result.status == "sent"
    assert closeout.delivery_result.confirmation_ref == "confirm-001"
    assert closeout.status == "closed"
    assert closeout.todo_items == []
    assert closeout.reason == "delivery confirmed: confirm-001"


def test_failed_delivery_creates_todo_item():
    task = _full_chain_task()
    intent = resolve_report_intent(task)
    closeout = closeout_lifecycle(task, intent, delivery_success=False, delivery_error="simulated smtp failure")

    assert closeout.delivery_result is not None
    assert closeout.delivery_result.status == "failed"
    assert closeout.delivery_result.error == "simulated smtp failure"
    assert closeout.status == "delivery_failed"
    assert len(closeout.todo_items) == 1
    assert closeout.todo_items[0].category == "delivery_failed"
    assert "simulated smtp failure" in closeout.reason


def test_incomplete_report_intent_creates_todo_without_delivery():
    task = WorkflowTask(
        task_id="task-msg-002",
        message_id="msg-002",
        group_id="GROUP001",
        project_id="chaoyang_steel",
        target_sop_node="",
        watch_item={},
        status="planned",
        reason="missing workflow fields",
    )
    intent = resolve_report_intent(task)
    closeout = closeout_lifecycle(task, intent)

    assert intent.status == "incomplete"
    assert "target_sop_node" in intent.missing_fields
    assert "watch_item" in intent.missing_fields
    assert closeout.delivery_result is None
    assert closeout.status == "report_intent_incomplete"
    assert len(closeout.todo_items) == 1
    assert closeout.todo_items[0].category == "incomplete_report_intent"
    assert "target_sop_node" in closeout.reason
