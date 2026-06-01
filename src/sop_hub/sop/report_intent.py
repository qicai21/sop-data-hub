"""Local report-intent resolver for SOP workflow tasks.

This module stays local-only:
- convert WorkflowTask into a ReportIntent;
- surface missing fields explicitly;
- keep report-template / recipient resolution local to functional tests;
- do not send reports, touch runtime, DB, wx-ops-agent, or delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sop_hub.sop.workflow_task import WorkflowTask


DEV_RECIPIENT_TARGET = {"type": "contact", "name": "郭东北"}

PROJECT_REPORT_CONFIG = {
    "zhongtang_special_steel": {
        "report_type": "departure_report",
        "template_path": "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/ztsteel_departure_report_template.xlsx",
        "recipient_target": DEV_RECIPIENT_TARGET,
        "required_fields": ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"],
    },
    "chaoyang_steel": {
        "report_type": "departure_report",
        "template_path": "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/cysteel_departure_report_template.xlsx",
        "recipient_target": DEV_RECIPIENT_TARGET,
        "required_fields": ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"],
    },
    "jilin_jingang_jinzhou": {
        "report_type": "departure_report",
        "template_path": "/Users/qicai21/projects/repos/sop-data-hub/config/report_templates/jilin_jingang_departure_report_template.xlsx",
        "recipient_target": DEV_RECIPIENT_TARGET,
        "required_fields": ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"],
    },
}


@dataclass(frozen=True)
class ReportIntent:
    template_path: str | None
    recipient_target: dict[str, Any] | None
    required_fields: list[str]
    missing_fields: list[str]
    report_type: str
    status: str
    message_id: str = ""
    group_id: str = ""
    project_id: str = ""
    target_sop_node: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_path": self.template_path,
            "recipient_target": self.recipient_target,
            "required_fields": list(self.required_fields),
            "missing_fields": list(self.missing_fields),
            "report_type": self.report_type,
            "status": self.status,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "target_sop_node": self.target_sop_node,
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


def _missing_required_fields(task: WorkflowTask, required_fields: list[str]) -> list[str]:
    missing: list[str] = []
    for field_name in required_fields:
        if field_name == "message_id" and not _value_present(task.message_id):
            missing.append(field_name)
        elif field_name == "group_id" and not _value_present(task.group_id):
            missing.append(field_name)
        elif field_name == "project_id" and not _value_present(task.project_id):
            missing.append(field_name)
        elif field_name == "target_sop_node" and not _value_present(task.target_sop_node):
            missing.append(field_name)
        elif field_name == "watch_item" and not _value_present(task.watch_item):
            missing.append(field_name)
    return missing


def resolve_report_intent(task: WorkflowTask) -> ReportIntent:
    """Resolve a local report intent from a workflow task."""

    project_id = (task.project_id or "").strip()
    config = PROJECT_REPORT_CONFIG.get(project_id)
    if config is None:
        required_fields = ["message_id", "group_id", "project_id", "target_sop_node", "watch_item"]
        missing_fields = _missing_required_fields(task, required_fields)
        if "project_id" not in missing_fields:
            missing_fields.append("project_id")
        return ReportIntent(
            template_path=None,
            recipient_target=None,
            required_fields=required_fields,
            missing_fields=missing_fields,
            report_type="unknown_report",
            status="unknown_project",
            message_id=task.message_id,
            group_id=task.group_id,
            project_id=task.project_id,
            target_sop_node=task.target_sop_node,
        )

    required_fields = list(config["required_fields"])
    missing_fields = _missing_required_fields(task, required_fields)
    status = "ready" if not missing_fields else "incomplete"
    return ReportIntent(
        template_path=config["template_path"],
        recipient_target=dict(config["recipient_target"]),
        required_fields=required_fields,
        missing_fields=missing_fields,
        report_type=config["report_type"],
        status=status,
        message_id=task.message_id,
        group_id=task.group_id,
        project_id=task.project_id,
        target_sop_node=task.target_sop_node,
    )
