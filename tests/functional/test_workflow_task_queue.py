"""Functional tests for the workflow-task queue planner.

Scope:
- local planner only
- no runtime, wx-ops-agent, database, 95306, OCR execution, report sending, or delivery
"""

from pathlib import Path

from ops_hub.sop.monitoring_plan_matcher import MessageEvent, match_message_event
from ops_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview
from ops_hub.sop.raw_asset_bundle import bind_raw_asset_bundle, register_raw_asset_bundle
from ops_hub.sop.workflow_task import build_workflow_task_queue


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sops"


def _plan():
    return build_real_sop_monitoring_plan_preview(FIXTURE_DIR).plan


def test_matched_message_creates_workflow_tasks_for_ordinary_freight_projects():
    event = MessageEvent(
        message_id="msg-task-a",
        channel="wechat",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T12:00:00Z",
        message_type="image",
        text="出港计划通知单",
    )
    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        raw_image_path="/tmp/sop/raw/task-a.png",
        ocr_json_path="/tmp/sop/raw/task-a.ocr.json",
        message_metadata_path="/tmp/sop/raw/task-a.meta.json",
        text=event.text,
        extraction_kind="image",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)
    match_result = match_message_event(bound_event, _plan())

    queue = build_workflow_task_queue(bound_event, bundle, match_result)

    assert queue.event.raw_asset_bundle == bundle
    assert queue.raw_asset_bundle == bundle
    assert queue.match_result == match_result
    assert queue.todo_items == []
    assert queue.workflow_tasks
    assert {task.message_id for task in queue.workflow_tasks} == {event.message_id}
    assert {task.group_id for task in queue.workflow_tasks} == {"GROUP001"}
    assert {task.project_id for task in queue.workflow_tasks} == {
        "zhongtang_special_steel",
        "chaoyang_steel",
        "jilin_jingang_jinzhou",
    }
    assert all(task.target_sop_node for task in queue.workflow_tasks)
    assert all(task.watch_item["document_type"] == "出港计划通知单" for task in queue.workflow_tasks)
    assert all(task.status == "planned" for task in queue.workflow_tasks)


def test_no_match_creates_todo_item():
    event = MessageEvent(
        message_id="msg-task-b",
        channel="wechat",
        group_id="GROUP999",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T12:01:00Z",
        message_type="text",
        text="未知消息",
    )
    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        message_metadata_path="/tmp/sop/raw/task-b.meta.json",
        text=event.text,
        extraction_kind="text",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)
    match_result = match_message_event(bound_event, _plan())

    queue = build_workflow_task_queue(bound_event, bundle, match_result)

    assert queue.workflow_tasks == []
    assert len(queue.todo_items) == 1
    todo = queue.todo_items[0]
    assert todo.category == "no_match"
    assert todo.message_id == event.message_id
    assert todo.group_id == "GROUP999"
    assert "no monitoring plan" in todo.reason or "no watch item matched" in todo.reason
    assert todo.raw_asset_bundle == bundle


def test_incomplete_asset_bundle_creates_todo_item_even_when_match_exists():
    event = MessageEvent(
        message_id="msg-task-c",
        channel="wechat",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T12:02:00Z",
        message_type="image",
        text="检装车通知单",
    )
    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        raw_image_path="/tmp/sop/raw/task-c.png",
        ocr_json_path=None,
        message_metadata_path="/tmp/sop/raw/task-c.meta.json",
        text=event.text,
        extraction_kind="image",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)
    match_result = match_message_event(bound_event, _plan())

    queue = build_workflow_task_queue(bound_event, bundle, match_result)

    assert queue.workflow_tasks
    assert any(task.project_id in {"zhongtang_special_steel", "chaoyang_steel"} for task in queue.workflow_tasks)
    assert len(queue.todo_items) == 1
    todo = queue.todo_items[0]
    assert todo.category == "incomplete_registration"
    assert "missing ocr_json_path" in todo.reason
    assert todo.raw_asset_bundle == bundle
