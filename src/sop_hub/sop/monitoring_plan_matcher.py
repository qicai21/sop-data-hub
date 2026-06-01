"""Message-event matcher for real SOP monitoring plans.

This module is intentionally thin:
- accept simulated message events;
- match them against the compiled `wechat_monitoring_plan`;
- return candidate projects and SOP node mappings.

It does not call WeChat, runtime, database, 95306, OCR, or report sending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sop_hub.sop.raw_asset_bundle import RawAssetBundle


@dataclass(frozen=True)
class MessageEvent:
    message_id: str
    channel: str
    group_id: str | None = None
    source_agent: str = ""
    received_at: str | None = None
    message_type: str = "text"
    text: str = ""
    raw_asset_bundle: RawAssetBundle | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitoringMatch:
    group_id: str
    watch_item: dict[str, Any]
    candidate_projects: list[str] = field(default_factory=list)
    target_sop_nodes: dict[str, list[str]] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class MessageMatchResult:
    event: MessageEvent
    matches: list[MonitoringMatch] = field(default_factory=list)
    reason: str = ""


DOCUMENT_MESSAGE_TYPES = {"image", "document"}
TEXT_MESSAGE_TYPES = {"text"}


def _group_plan_for_event(event: MessageEvent, wechat_plan: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    if event.group_id and event.group_id in wechat_plan:
        return event.group_id, wechat_plan[event.group_id]

    group_name = str(event.metadata.get("group_name") or event.group_id or "").strip()
    if not group_name:
        return None, None

    for group_id, group_plan in wechat_plan.items():
        plan_group_name = str(group_plan.get("group_name") or "").strip()
        # Exact match
        if plan_group_name == group_name:
            return group_id, group_plan
        # Substring match (dir name often has -GROUPxxx suffix, plan has clean name)
        if plan_group_name and plan_group_name in group_name:
            return group_id, group_plan
    return None, None


def _synthetic_watch_item(*, project_id: str, target_sop_node: str, anchor_text: str) -> dict[str, Any]:
    return {
        "input_type": "text",
        "message_type": anchor_text,
        "text_patterns": [anchor_text],
        "candidate_projects": [project_id],
        "target_sop_nodes": {project_id: [target_sop_node]},
    }


def _fallback_alignment_match(
    event: MessageEvent,
    *,
    group_id: str,
    group_plan: dict[str, Any],
    event_text: str,
) -> MonitoringMatch | None:
    project_id: str | None = None
    anchor_text: str | None = None

    if group_id == "GROUP003":
        if any(token in event_text for token in ("汐子", "放货", "实装")):
            project_id = "zhongtang_special_steel"
            anchor_text = "汐子放货"
    elif group_id == "GROUP001":
        if any(token in event_text for token in ("四平铁矿箱", "四平放货", "四平")):
            project_id = "jilin_jingang_jinzhou"
            anchor_text = "四平铁矿箱"
        elif any(token in event_text for token in ("朝阳西", "朝阳")):
            project_id = "chaoyang_steel"
            anchor_text = "朝阳西"
        elif "汐子" in event_text:
            project_id = "chaoyang_steel"
            anchor_text = "汐子"
    elif group_id == "GROUP013":
        # R19: 朝阳西/木森17 messages in 数据单发群 align to chaoyang_steel
        if any(token in event_text for token in ("朝阳西", "木森17")):
            project_id = "chaoyang_steel"
            anchor_text = "朝阳西木森17"

    if not project_id or not anchor_text:
        return None

    target_sop_nodes: dict[str, list[str]] = {}
    for watch_item in group_plan.get("watch_items") or []:
        node_ids = list((watch_item.get("target_sop_nodes") or {}).get(project_id) or [])
        if node_ids:
            target_sop_nodes[project_id] = node_ids
            break

    if not target_sop_nodes:
        target_sop_nodes[project_id] = [f"{project_id}:{group_id}:dashboard_alignment"]

    watch_item = _synthetic_watch_item(
        project_id=project_id,
        target_sop_node=target_sop_nodes[project_id][0],
        anchor_text=anchor_text,
    )
    return MonitoringMatch(
        group_id=group_id,
        watch_item=watch_item,
        candidate_projects=[project_id],
        target_sop_nodes=target_sop_nodes,
        reason=f"fallback matched real message anchor {anchor_text!r} for project_id {project_id}",
    )

def _normalized_text(event: MessageEvent) -> str:

    if event.text:
        return event.text
    if event.raw_asset_bundle:
        if event.raw_asset_bundle.text:
            return event.raw_asset_bundle.text
        for asset_path in (
            event.raw_asset_bundle.raw_image_path,
            event.raw_asset_bundle.ocr_json_path,
            event.raw_asset_bundle.message_metadata_path,
        ):
            if asset_path:
                return Path(asset_path).stem
    return ""


def _match_document_item(event_text: str, watch_item: dict[str, Any]) -> bool:
    document_type = watch_item.get("document_type")
    return bool(document_type and document_type in event_text)


def _match_text_item(event_text: str, watch_item: dict[str, Any]) -> bool:
    message_type = watch_item.get("message_type")
    if message_type and message_type in event_text:
        return True
    for pattern in watch_item.get("text_patterns") or []:
        if pattern and pattern in event_text:
            return True
    return False


def match_message_event(event: MessageEvent, monitoring_plan: dict[str, Any]) -> MessageMatchResult:
    """Match a single message event against a compiled monitoring plan."""

    wechat_plan = monitoring_plan.get("wechat_monitoring_plan") or {}
    group_id, group_plan = _group_plan_for_event(event, wechat_plan)
    if not group_plan or not group_id:
        if event.group_id:
            return MessageMatchResult(event=event, reason=f"no monitoring plan for group_id {event.group_id}")
        return MessageMatchResult(event=event, reason="missing group_id")

    event_text = _normalized_text(event)
    matches: list[MonitoringMatch] = []
    for watch_item in group_plan.get("watch_items") or []:
        if event.message_type in DOCUMENT_MESSAGE_TYPES:
            matched = _match_document_item(event_text, watch_item)
        elif event.message_type in TEXT_MESSAGE_TYPES:
            matched = _match_text_item(event_text, watch_item)
        else:
            matched = _match_document_item(event_text, watch_item) or _match_text_item(event_text, watch_item)

        if not matched:
            continue

        matches.append(
            MonitoringMatch(
                group_id=group_id,
                watch_item=watch_item,
                candidate_projects=list(watch_item.get("candidate_projects") or []),
                target_sop_nodes={project_id: list(node_ids) for project_id, node_ids in (watch_item.get("target_sop_nodes") or {}).items()},
                reason=f"matched group_id {group_id} and watch item",
            )
        )

    if not matches:
        fallback_match = _fallback_alignment_match(event, group_id=group_id, group_plan=group_plan, event_text=event_text)
        if fallback_match is not None:
            return MessageMatchResult(event=event, matches=[fallback_match])
        return MessageMatchResult(
            event=event,
            reason=f"no watch item matched group_id {event.group_id} and text {event_text!r}",
        )

    return MessageMatchResult(event=event, matches=matches)


def result_to_dict(result: MessageMatchResult) -> dict[str, Any]:
    """Serialize a match result into plain dictionaries."""

    return {
        "event": {
            "message_id": result.event.message_id,
            "channel": result.event.channel,
            "group_id": result.event.group_id,
            "source_agent": result.event.source_agent,
            "received_at": result.event.received_at,
            "message_type": result.event.message_type,
            "text": result.event.text,
            "raw_asset_bundle": result.event.raw_asset_bundle.to_dict() if result.event.raw_asset_bundle else None,
            "metadata": dict(result.event.metadata),
        },
        "matches": [
            {
                "group_id": match.group_id,
                "watch_item": match.watch_item,
                "candidate_projects": match.candidate_projects,
                "target_sop_nodes": match.target_sop_nodes,
                "reason": match.reason,
            }
            for match in result.matches
        ],
        "reason": result.reason,
    }
