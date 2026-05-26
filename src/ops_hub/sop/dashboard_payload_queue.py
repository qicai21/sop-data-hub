"""Dashboard payload queue helpers for R15 ordinary-freight payload emission.

This module stays local-only:
- convert WorkflowTask + DashboardIntent into a dashboard payload record;
- write JSON payloads under `runtime/dashboard_intents/`;
- keep the payload output consumable without touching runtime daemons,
  database tables, dashboard HTML, or report delivery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_hub.sop.dashboard_intent import DashboardIntent, resolve_dashboard_intent
from ops_hub.sop.workflow_task import WorkflowTask, WorkflowTaskQueue


DEFAULT_DASHBOARD_INTENT_DIR = Path("runtime/dashboard_intents")
PAYLOAD_VERSION = "r15"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _payload_source(task: WorkflowTask) -> str:
    bundle = task.raw_asset_bundle
    if bundle is not None:
        metadata_path = bundle.message_metadata_path
        if _value_present(metadata_path):
            return str(metadata_path)
        if _value_present(bundle.raw_image_path):
            return str(bundle.raw_image_path)
    return ""


@dataclass(frozen=True)
class DashboardPayload:
    message_id: str
    project_id: str
    group_id: str
    watch_item: dict[str, Any]
    target_sop_node: str
    dashboard_action: str
    created_at: str
    source: str
    status: str
    payload_version: str = PAYLOAD_VERSION
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "project_id": self.project_id,
            "group_id": self.group_id,
            "watch_item": self.watch_item,
            "target_sop_node": self.target_sop_node,
            "dashboard_action": self.dashboard_action,
            "created_at": self.created_at,
            "source": self.source,
            "status": self.status,
            "payload_version": self.payload_version,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DashboardPayloadQueue:
    event_message_id: str
    group_id: str
    dashboard_intents: list[DashboardIntent] = field(default_factory=list)
    dashboard_payloads: list[DashboardPayload] = field(default_factory=list)
    output_dir: Path = DEFAULT_DASHBOARD_INTENT_DIR
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_message_id": self.event_message_id,
            "group_id": self.group_id,
            "dashboard_intents": [intent.to_dict() for intent in self.dashboard_intents],
            "dashboard_payloads": [payload.to_dict() for payload in self.dashboard_payloads],
            "output_dir": str(self.output_dir),
            "reason": self.reason,
        }


def resolve_dashboard_payload(
    task: WorkflowTask,
    intent: DashboardIntent | None = None,
    *,
    created_at: str | None = None,
    payload_version: str = PAYLOAD_VERSION,
) -> DashboardPayload:
    intent = intent or resolve_dashboard_intent(task)
    created_at = created_at or _utc_now_iso()
    return DashboardPayload(
        message_id=task.message_id,
        project_id=task.project_id,
        group_id=task.group_id,
        watch_item=dict(task.watch_item),
        target_sop_node=task.target_sop_node,
        dashboard_action=intent.dashboard_action,
        created_at=created_at,
        source=_payload_source(task),
        status=intent.status,
        payload_version=payload_version,
        reason=intent.reason,
    )


def build_dashboard_payload_queue(
    task_queue: WorkflowTaskQueue,
    *,
    created_at: str | None = None,
    payload_version: str = PAYLOAD_VERSION,
    output_dir: str | Path = DEFAULT_DASHBOARD_INTENT_DIR,
) -> DashboardPayloadQueue:
    dashboard_intents: list[DashboardIntent] = []
    dashboard_payloads: list[DashboardPayload] = []
    for task in task_queue.workflow_tasks:
        intent = resolve_dashboard_intent(task)
        dashboard_intents.append(intent)
        if intent.status != "ready":
            continue
        dashboard_payloads.append(
            resolve_dashboard_payload(
                task,
                intent,
                created_at=created_at,
                payload_version=payload_version,
            )
        )

    return DashboardPayloadQueue(
        event_message_id=task_queue.event.message_id,
        group_id=task_queue.event.group_id or "",
        dashboard_intents=dashboard_intents,
        dashboard_payloads=dashboard_payloads,
        output_dir=Path(output_dir),
        reason=task_queue.reason,
    )


def write_dashboard_payload_queue(
    queue: DashboardPayloadQueue,
    *,
    output_dir: str | Path | None = None,
) -> list[Path]:
    output_path = Path(output_dir) if output_dir is not None else queue.output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    message_id_counts: dict[str, int] = {}
    for payload in queue.dashboard_payloads:
        message_id_counts[payload.message_id] = message_id_counts.get(payload.message_id, 0) + 1

    written_paths: list[Path] = []
    for payload in queue.dashboard_payloads:
        suffix = f"__{payload.project_id}" if message_id_counts.get(payload.message_id, 0) > 1 else ""
        path = output_path / f"{payload.message_id}{suffix}.json"
        path.write_text(json.dumps(payload.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        written_paths.append(path)
    return written_paths