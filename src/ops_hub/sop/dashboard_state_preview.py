"""Dashboard state preview helpers for ordinary-freight dashboard consumption.

This module stays local-only:
- consume DashboardPayloadQueue records produced under `runtime/dashboard_intents/`;
- project each payload into a dashboard state snapshot;
- write JSON previews under `runtime/dashboard_state/`;
- do not touch dashboard HTML, database, runtime daemon, wx-ops-agent, or report delivery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops_hub.sop.dashboard_payload_queue import DashboardPayload, DashboardPayloadQueue


DEFAULT_DASHBOARD_STATE_DIR = Path("runtime/dashboard_state")
STATE_VERSION = "r16"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _state_nodes(payload: DashboardPayload) -> list[str]:
    watch_nodes = (payload.watch_item.get("target_sop_nodes") or {}).get(payload.project_id) or []
    if not watch_nodes and payload.target_sop_node:
        watch_nodes = [payload.target_sop_node]
    return _unique_strings([str(node) for node in watch_nodes if str(node).strip()])


@dataclass(frozen=True)
class DashboardState:
    project_id: str
    current_nodes: list[str]
    latest_message_id: str
    watch_item: dict[str, Any]
    updated_at: str
    status: str
    source: str
    state_version: str = STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "current_nodes": self.current_nodes,
            "latest_message_id": self.latest_message_id,
            "watch_item": self.watch_item,
            "updated_at": self.updated_at,
            "status": self.status,
            "source": self.source,
            "state_version": self.state_version,
        }


@dataclass(frozen=True)
class DashboardConsumerPreview:
    dashboard_payload_queue: DashboardPayloadQueue
    dashboard_states: list[DashboardState] = field(default_factory=list)
    output_dir: Path = DEFAULT_DASHBOARD_STATE_DIR
    state_version: str = STATE_VERSION
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dashboard_payload_queue": self.dashboard_payload_queue.to_dict(),
            "dashboard_states": [state.to_dict() for state in self.dashboard_states],
            "output_dir": str(self.output_dir),
            "state_version": self.state_version,
            "reason": self.reason,
        }


def build_dashboard_state_preview(
    payload_queue: DashboardPayloadQueue,
    *,
    updated_at: str | None = None,
    state_version: str = STATE_VERSION,
    output_dir: str | Path = DEFAULT_DASHBOARD_STATE_DIR,
) -> DashboardConsumerPreview:
    dashboard_states: list[DashboardState] = []
    for payload in payload_queue.dashboard_payloads:
        if payload.status != "ready":
            continue
        dashboard_states.append(
            DashboardState(
                project_id=payload.project_id,
                current_nodes=_state_nodes(payload),
                latest_message_id=payload.message_id,
                watch_item=dict(payload.watch_item),
                updated_at=updated_at or payload.created_at,
                status="active",
                source=payload.source,
                state_version=state_version,
            )
        )

    return DashboardConsumerPreview(
        dashboard_payload_queue=payload_queue,
        dashboard_states=dashboard_states,
        output_dir=Path(output_dir),
        state_version=state_version,
        reason=payload_queue.reason,
    )


def write_dashboard_state_preview(
    preview: DashboardConsumerPreview,
    *,
    output_dir: str | Path | None = None,
) -> list[Path]:
    output_path = Path(output_dir) if output_dir is not None else preview.output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    message_id_counts: dict[str, int] = {}
    for state in preview.dashboard_states:
        message_id_counts[state.latest_message_id] = message_id_counts.get(state.latest_message_id, 0) + 1

    written_paths: list[Path] = []
    for state in preview.dashboard_states:
        suffix = f"__{state.project_id}" if message_id_counts.get(state.latest_message_id, 0) > 1 else ""
        path = output_path / f"{state.latest_message_id}{suffix}.json"
        path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        written_paths.append(path)
    return written_paths
