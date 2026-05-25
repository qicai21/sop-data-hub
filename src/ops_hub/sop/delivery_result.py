"""Local delivery-result and lifecycle closeout helpers for SOP workflow tasks.

This module stays local-only:
- simulate a delivery result from a resolved ReportIntent;
- close the lifecycle when delivery succeeds;
- create todo items when delivery fails or the report intent is incomplete;
- do not send reports, touch runtime, DB, wx-ops-agent, OCR, 95306, or asset
  movement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ops_hub.sop.report_intent import ReportIntent
from ops_hub.sop.workflow_task import TodoItem, WorkflowTask


@dataclass(frozen=True)
class DeliveryResult:
    delivery_id: str
    message_id: str
    project_id: str
    target_sop_node: str
    report_type: str
    recipient_target: dict[str, Any] | None
    status: str
    confirmation_ref: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "message_id": self.message_id,
            "project_id": self.project_id,
            "target_sop_node": self.target_sop_node,
            "report_type": self.report_type,
            "recipient_target": self.recipient_target,
            "status": self.status,
            "confirmation_ref": self.confirmation_ref,
            "error": self.error,
        }


@dataclass(frozen=True)
class LifecycleCloseout:
    workflow_task: WorkflowTask
    report_intent: ReportIntent
    delivery_result: DeliveryResult | None
    status: str
    todo_items: list[TodoItem] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_task": self.workflow_task.to_dict(),
            "report_intent": self.report_intent.to_dict(),
            "delivery_result": self.delivery_result.to_dict() if self.delivery_result else None,
            "status": self.status,
            "todo_items": [todo.to_dict() for todo in self.todo_items],
            "reason": self.reason,
        }


def _delivery_id(task: WorkflowTask, report_intent: ReportIntent) -> str:
    return f"{task.message_id}:{task.project_id}:{task.target_sop_node}:{report_intent.report_type}"


def _confirmation_ref(delivery_id: str, confirmation_ref: str | None) -> str:
    if confirmation_ref:
        return confirmation_ref
    return f"confirmed:{delivery_id}"


def simulate_delivery_result(
    task: WorkflowTask,
    report_intent: ReportIntent,
    *,
    success: bool = True,
    confirmation_ref: str | None = None,
    error: str = "",
) -> DeliveryResult:
    """Return a simulated delivery result for local functional tests."""

    delivery_id = _delivery_id(task, report_intent)
    if report_intent.status != "ready":
        return DeliveryResult(
            delivery_id=delivery_id,
            message_id=task.message_id,
            project_id=task.project_id,
            target_sop_node=task.target_sop_node,
            report_type=report_intent.report_type,
            recipient_target=report_intent.recipient_target,
            status="skipped",
            confirmation_ref="",
            error=error or "report intent is not ready",
        )

    if success:
        return DeliveryResult(
            delivery_id=delivery_id,
            message_id=task.message_id,
            project_id=task.project_id,
            target_sop_node=task.target_sop_node,
            report_type=report_intent.report_type,
            recipient_target=report_intent.recipient_target,
            status="sent",
            confirmation_ref=_confirmation_ref(delivery_id, confirmation_ref),
            error="",
        )

    return DeliveryResult(
        delivery_id=delivery_id,
        message_id=task.message_id,
        project_id=task.project_id,
        target_sop_node=task.target_sop_node,
        report_type=report_intent.report_type,
        recipient_target=report_intent.recipient_target,
        status="failed",
        confirmation_ref="",
        error=error or "simulated delivery failure",
    )


def _todo_from_failure(task: WorkflowTask, report_intent: ReportIntent, reason: str) -> TodoItem:
    category = "delivery_failed" if report_intent.status == "ready" else "incomplete_report_intent"
    return TodoItem(
        todo_id=f"{task.message_id}:{task.group_id}:{category}",
        message_id=task.message_id,
        group_id=task.group_id,
        category=category,
        reason=reason,
        raw_asset_bundle=task.raw_asset_bundle,
        suggested_action=(
            "retry the simulated delivery" if report_intent.status == "ready" else "fill missing report-intent fields before delivery"
        ),
        status="open",
    )


def closeout_lifecycle(
    task: WorkflowTask,
    report_intent: ReportIntent,
    *,
    delivery_success: bool = True,
    confirmation_ref: str | None = None,
    delivery_error: str = "",
) -> LifecycleCloseout:
    """Close out a workflow task with a simulated delivery outcome."""

    if report_intent.status != "ready":
        todo_item = _todo_from_failure(
            task,
            report_intent,
            reason=f"report intent incomplete: {', '.join(report_intent.missing_fields) or 'unknown missing fields'}",
        )
        return LifecycleCloseout(
            workflow_task=task,
            report_intent=report_intent,
            delivery_result=None,
            status="report_intent_incomplete",
            todo_items=[todo_item],
            reason=todo_item.reason,
        )

    delivery_result = simulate_delivery_result(
        task,
        report_intent,
        success=delivery_success,
        confirmation_ref=confirmation_ref,
        error=delivery_error,
    )
    if delivery_result.status == "sent":
        return LifecycleCloseout(
            workflow_task=task,
            report_intent=report_intent,
            delivery_result=delivery_result,
            status="closed",
            todo_items=[],
            reason=f"delivery confirmed: {delivery_result.confirmation_ref}",
        )

    todo_item = _todo_from_failure(
        task,
        report_intent,
        reason=delivery_result.error or "simulated delivery failure",
    )
    return LifecycleCloseout(
        workflow_task=task,
        report_intent=report_intent,
        delivery_result=delivery_result,
        status="delivery_failed",
        todo_items=[todo_item],
        reason=todo_item.reason,
    )
