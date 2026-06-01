"""Local dashboard-intent helpers for ordinary-freight alignment.

This module stays local-only:
- convert WorkflowTask into a dashboard payload intent;
- preserve project / message / SOP-node links;
- do not write dashboard HTML, touch runtime, database, or report delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sop_hub.sop.workflow_task import WorkflowTask


ORDINARY_FREIGHT_PROJECTS = {
    "zhongtang_special_steel": "中唐特钢",
    "chaoyang_steel": "朝阳钢铁",
    "jilin_jingang_jinzhou": "吉林金钢",
}


@dataclass(frozen=True)
class DashboardIntent:
    project_id: str
    message_id: str
    watch_item: dict[str, Any]
    target_sop_node: str
    status: str
    dashboard_action: str
    group_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "message_id": self.message_id,
            "watch_item": self.watch_item,
            "target_sop_node": self.target_sop_node,
            "status": self.status,
            "dashboard_action": self.dashboard_action,
            "group_id": self.group_id,
            "reason": self.reason,
        }


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def _missing_required_fields(task: WorkflowTask) -> list[str]:
    missing: list[str] = []
    if not _value_present(task.message_id):
        missing.append("message_id")
    if not _value_present(task.group_id):
        missing.append("group_id")
    if not _value_present(task.project_id):
        missing.append("project_id")
    if not _value_present(task.target_sop_node):
        missing.append("target_sop_node")
    if not _value_present(task.watch_item):
        missing.append("watch_item")
    return missing


def resolve_dashboard_intent(task: WorkflowTask) -> DashboardIntent:
    """Resolve a dashboard payload intent from a workflow task."""

    project_id = (task.project_id or "").strip()
    missing_fields = _missing_required_fields(task)
    if project_id not in ORDINARY_FREIGHT_PROJECTS:
        status = "unknown_project"
        dashboard_action = "flag_review"
        if "project_id" not in missing_fields:
            missing_fields.append("project_id")
    elif missing_fields:
        status = "incomplete"
        dashboard_action = "flag_review"
    else:
        status = "ready"
        dashboard_action = "upsert_payload"

    reason = ""
    if status != "ready":
        reason = f"dashboard intent {status}: {', '.join(missing_fields) or 'unknown reason'}"
    elif task.reason:
        reason = task.reason

    return DashboardIntent(
        project_id=task.project_id,
        message_id=task.message_id,
        watch_item=dict(task.watch_item),
        target_sop_node=task.target_sop_node,
        status=status,
        dashboard_action=dashboard_action,
        group_id=task.group_id,
        reason=reason,
    )
