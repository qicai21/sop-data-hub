"""Local workflow-task planner for SOP monitoring matches.

This module stays local-only:
- convert message match results into workflow tasks;
- convert no-match or incomplete asset cases into todo items;
- preserve message / group / project / SOP-node links without touching runtime,
  database, wx-ops-agent, 95306, OCR execution, or report delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ops_hub.sop.monitoring_plan_matcher import MessageEvent, MessageMatchResult
from ops_hub.sop.raw_asset_bundle import RawAssetBundle, bind_raw_asset_bundle


@dataclass(frozen=True)
class WorkflowTask:
    task_id: str
    message_id: str
    group_id: str
    project_id: str
    target_sop_node: str
    watch_item: dict[str, Any]
    raw_asset_bundle: RawAssetBundle | None = None
    status: str = "planned"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "target_sop_node": self.target_sop_node,
            "watch_item": self.watch_item,
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TodoItem:
    todo_id: str
    message_id: str
    group_id: str
    category: str
    reason: str
    raw_asset_bundle: RawAssetBundle | None = None
    suggested_action: str = ""
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "category": self.category,
            "reason": self.reason,
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "suggested_action": self.suggested_action,
            "status": self.status,
        }


@dataclass(frozen=True)
class WorkflowTaskQueue:
    event: MessageEvent
    raw_asset_bundle: RawAssetBundle | None
    match_result: MessageMatchResult
    workflow_tasks: list[WorkflowTask] = field(default_factory=list)
    todo_items: list[TodoItem] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": {
                "message_id": self.event.message_id,
                "channel": self.event.channel,
                "group_id": self.event.group_id,
                "source_agent": self.event.source_agent,
                "received_at": self.event.received_at,
                "message_type": self.event.message_type,
                "text": self.event.text,
                "raw_asset_bundle": self.event.raw_asset_bundle.to_dict() if self.event.raw_asset_bundle else None,
            },
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "match_result": {
                "reason": self.match_result.reason,
                "matches": [
                    {
                        "group_id": match.group_id,
                        "watch_item": match.watch_item,
                        "candidate_projects": match.candidate_projects,
                        "target_sop_nodes": match.target_sop_nodes,
                        "reason": match.reason,
                    }
                    for match in self.match_result.matches
                ],
            },
            "workflow_tasks": [task.to_dict() for task in self.workflow_tasks],
            "todo_items": [todo.to_dict() for todo in self.todo_items],
            "reason": self.reason,
        }


def _task_id(message_id: str, group_id: str, project_id: str, target_sop_node: str) -> str:
    return f"{message_id}:{group_id}:{project_id}:{target_sop_node}"


def _todo_id(message_id: str, group_id: str, category: str) -> str:
    return f"{message_id}:{group_id}:{category}"


def _bundle_for_event(event: MessageEvent, raw_asset_bundle: RawAssetBundle | None) -> RawAssetBundle | None:
    if raw_asset_bundle is not None:
        return raw_asset_bundle
    return event.raw_asset_bundle


def _queued_event(event: MessageEvent, bundle: RawAssetBundle | None) -> MessageEvent:
    if bundle is None or event.raw_asset_bundle == bundle:
        return event
    return bind_raw_asset_bundle(event, bundle)


def _missing_bundle_category(bundle: RawAssetBundle | None) -> str:
    if bundle is None:
        return "missing_asset"
    if bundle.registration_status != "complete":
        return "incomplete_registration"
    return "missing_asset"


def _missing_bundle_reason(bundle: RawAssetBundle | None) -> str:
    if bundle is None:
        return "raw asset bundle is missing"
    if bundle.warnings:
        return "; ".join(bundle.warnings)
    return f"raw asset bundle registration_status={bundle.registration_status}"


def build_workflow_task_queue(
    event: MessageEvent,
    raw_asset_bundle: RawAssetBundle | None,
    match_result: MessageMatchResult,
) -> WorkflowTaskQueue:
    """Create workflow tasks for matches and todo items for no-match / incomplete cases."""

    bundle = _bundle_for_event(event, raw_asset_bundle)
    queued_event = _queued_event(event, bundle)

    workflow_tasks: list[WorkflowTask] = []
    todo_items: list[TodoItem] = []
    seen_task_keys: set[tuple[str, str, str]] = set()

    if match_result.matches:
        for match in match_result.matches:
            candidate_projects = match.candidate_projects or list(match.target_sop_nodes.keys())
            for project_id in candidate_projects:
                node_ids = list(match.target_sop_nodes.get(project_id) or [])
                if not node_ids:
                    continue
                for target_sop_node in node_ids:
                    task_key = (queued_event.message_id, project_id, target_sop_node)
                    if task_key in seen_task_keys:
                        continue
                    seen_task_keys.add(task_key)
                    workflow_tasks.append(
                        WorkflowTask(
                            task_id=_task_id(queued_event.message_id, queued_event.group_id or "", project_id, target_sop_node),
                            message_id=queued_event.message_id,
                            group_id=queued_event.group_id or "",
                            project_id=project_id,
                            target_sop_node=target_sop_node,
                            watch_item=match.watch_item,
                            raw_asset_bundle=bundle,
                            status="planned",
                            reason=match.reason,
                        )
                    )

    if not match_result.matches:
        todo_items.append(
            TodoItem(
                todo_id=_todo_id(queued_event.message_id, queued_event.group_id or "", "no_match"),
                message_id=queued_event.message_id,
                group_id=queued_event.group_id or "",
                category="no_match",
                reason=match_result.reason or f"no monitoring plan matched for group_id {queued_event.group_id}",
                raw_asset_bundle=bundle,
                suggested_action="review the message text and extend the monitoring plan or queue a manual follow-up",
                status="open",
            )
        )

    if bundle is None or bundle.registration_status != "complete":
        category = _missing_bundle_category(bundle)
        todo_items.append(
            TodoItem(
                todo_id=_todo_id(queued_event.message_id, queued_event.group_id or "", category),
                message_id=queued_event.message_id,
                group_id=queued_event.group_id or "",
                category=category,
                reason=_missing_bundle_reason(bundle),
                raw_asset_bundle=bundle,
                suggested_action="register the missing raw asset paths before retrying the planner",
                status="open",
            )
        )

    reason_parts = [part for part in [match_result.reason] if part]
    if bundle is not None and bundle.warnings:
        reason_parts.append("; ".join(bundle.warnings))
    reason = " | ".join(reason_parts)

    return WorkflowTaskQueue(
        event=queued_event,
        raw_asset_bundle=bundle,
        match_result=match_result,
        workflow_tasks=workflow_tasks,
        todo_items=todo_items,
        reason=reason,
    )
