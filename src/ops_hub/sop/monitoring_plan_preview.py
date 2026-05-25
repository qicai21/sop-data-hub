"""Preview adapter for real SOP monitoring plans.

This module is intentionally thin:
- convert normalized real SOP markdown fixtures into compiler input;
- run SopMonitoringPlanCompiler;
- render a human-readable preview report.

It does not call WeChat, 95306, runtime, DB, publisher, or OCR.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ops_hub.models.project_sop import NormalizedMonitoringEntry, NormalizedProjectSOP, load_normalized_project_sops
from ops_hub.sop.monitoring_plan_compiler import SopMonitoringPlanCompiler


REPORT_PATH = Path("reports/real_sop_monitoring_plan_preview_20260525.md")


@dataclass(frozen=True)
class PreviewResult:
    project_sops: list[dict]
    plan: dict
    normalized_projects: list[NormalizedProjectSOP]


def _has_group_token(entry: NormalizedMonitoringEntry) -> bool:
    return bool(entry.group_token)


def _build_node_id(project_id: str, group_token: str, index: int, entry: NormalizedMonitoringEntry) -> str:
    source_hint = entry.raw_source or "monitoring"
    safe_hint = "".join(ch if ch.isalnum() else "_" for ch in source_hint).strip("_")
    suffix = f"_{safe_hint}" if safe_hint else ""
    return f"{project_id}:{group_token}:{index}{suffix}"


def _build_monitoring_requirements(entry: NormalizedMonitoringEntry) -> list[dict]:
    requirements: list[dict] = []
    for document_type in entry.document_keywords:
        requirements.append(
            {
                "channel": "wechat",
                "group_id": entry.group_token,
                "group_name": entry.group_name,
                "input_type": "document",
                "document_type": document_type,
            }
        )
    for message_type in entry.message_keywords:
        requirements.append(
            {
                "channel": "wechat",
                "group_id": entry.group_token,
                "group_name": entry.group_name,
                "input_type": "text",
                "message_type": message_type,
                "text_patterns": [message_type],
            }
        )
    return requirements


def normalized_project_sops_to_compiler_input(normalized_project_sops: Sequence[NormalizedProjectSOP]) -> list[dict]:
    """Convert normalized project SOPs into the input accepted by the compiler."""

    project_sops: list[dict] = []
    for project in normalized_project_sops:
        sop_nodes = []
        for index, entry in enumerate(project.monitoring_entries, start=1):
            if not _has_group_token(entry):
                continue
            group_token = entry.group_token or ""
            monitoring = _build_monitoring_requirements(entry)
            if not monitoring:
                continue
            sop_nodes.append(
                {
                    "node_id": _build_node_id(project.project_id, group_token, index, entry),
                    "node_name": entry.group_name or entry.raw_source or project.project_name,
                    "monitoring": monitoring,
                }
            )
        if sop_nodes:
            project_sops.append(
                {
                    "project_id": project.project_id,
                    "project_name": project.project_name,
                    "sop_nodes": sop_nodes,
                }
            )
    return project_sops


def build_real_sop_monitoring_plan_preview(
    fixture_dir: str | Path,
) -> PreviewResult:
    normalized_projects = load_normalized_project_sops(fixture_dir)
    project_sops = normalized_project_sops_to_compiler_input(normalized_projects)
    plan = SopMonitoringPlanCompiler().compile(project_sops)
    return PreviewResult(
        project_sops=project_sops,
        plan=plan,
        normalized_projects=normalized_projects,
    )


def _format_list(values: Sequence[str]) -> str:
    if not values:
        return "-"
    return ", ".join(values)


def render_real_sop_monitoring_plan_preview_markdown(
    fixture_dir: str | Path,
    preview: PreviewResult | None = None,
) -> str:
    fixture_path = Path(fixture_dir)
    preview = preview or build_real_sop_monitoring_plan_preview(fixture_path)

    lines: list[str] = [
        "# Real SOP Monitoring Plan Preview",
        "",
        "## 1. Input fixtures",
    ]
    for project in preview.normalized_projects:
        lines.extend(
            [
                f"- `{project.source_path.name}` → `{project.project_id}` / {project.project_name}",
                f"  - title: {project.source_title}",
                f"  - groups: {_format_list(project.group_tokens)}",
            ]
        )

    lines.extend(
        [
            "",
            f"## 2. Normalized project count",
            f"- {len(preview.normalized_projects)}",
            "",
            "## 3. Generated wechat_monitoring_plan",
        ]
    )

    wechat_plan = preview.plan.get("wechat_monitoring_plan", {})
    if not wechat_plan:
        lines.append("- none")
    for group_id, group_plan in sorted(wechat_plan.items()):
        lines.extend(
            [
                f"### Group `{group_id}`",
                f"- group name: {group_plan.get('group_name') or '-'}",
            ]
        )
        for index, item in enumerate(group_plan.get("watch_items", []), start=1):
            lines.append(f"- watch item {index}")
            lines.append(f"  - input_type: {item.get('input_type')}")
            if item.get("document_type") is not None:
                lines.append(f"  - document_type: {item.get('document_type')}")
            if item.get("message_type") is not None:
                lines.append(f"  - message_type: {item.get('message_type')}")
            if item.get("text_patterns"):
                lines.append(f"  - text_patterns: {', '.join(item.get('text_patterns', []))}")
            lines.append(f"  - candidate_projects: {_format_list(item.get('candidate_projects', []))}")
            node_map = item.get("target_sop_nodes", {})
            if node_map:
                rendered_nodes = []
                for project_id, node_ids in node_map.items():
                    rendered_nodes.append(f"{project_id}: {', '.join(node_ids)}")
                lines.append(f"  - target_sop_nodes: {'; '.join(rendered_nodes)}")
            else:
                lines.append("  - target_sop_nodes: -")

    lines.extend(
        [
            "",
            "## 4. Limitations / warnings",
            "- This is a preview only; it does not call WeChat, 95306, runtime, publisher, or DB.",
            "- Entries without a WeChat group token are skipped by the adapter.",
            "- The preview uses extracted document/message keywords only; it does not add new business inference.",
            "",
            "## 5. Usability check",
            "- The generated plan is readable and sufficient for a next-round review, but it is not production-ready.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_real_sop_monitoring_plan_preview_report(
    fixture_dir: str | Path,
    output_path: str | Path = REPORT_PATH,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview = build_real_sop_monitoring_plan_preview(fixture_dir)
    output_path.write_text(
        render_real_sop_monitoring_plan_preview_markdown(fixture_dir, preview),
        encoding="utf-8",
    )
    return output_path
