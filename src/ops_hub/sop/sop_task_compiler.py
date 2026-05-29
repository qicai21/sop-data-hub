"""Compile SOP YAML flows into inspectable ExecutableTaskPlan.

Reads YAML directly (not through load_project_sop) because the current
ProjectSOP model only captures listening_tasks, not flows / runtime.

This module stays local-only:
- reads YAML and constructs task descriptors;
- does not execute tasks, write DB, query 95306, generate Excel, or send reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── Executor status registry ──────────────────────────────────────────────
# Maps action names to (status, evidence_file).
# Actions not in this registry default to "missing".

_EXECUTOR_STATUS: dict[str, tuple[str, str]] = {
    # ── implemented ──
    "parse_departure_text": (
        "implemented",
        "src/ops_hub/sop/departure_text_parser.py",
    ),
    "classify_message": (
        "implemented",
        "src/ops_hub/sop/monitoring_plan_matcher.py",
    ),
    "extract_release_notice_json": (
        "implemented",
        "src/ops_hub/runner.py (OCR → JSON pipeline)",
    ),
    "identify_project": (
        "implemented",
        "src/ops_hub/sop/monitoring_plan_matcher.py (_fallback_alignment_match)",
    ),
    "create_release_batch": (
        "implemented",
        "src/ops_hub/data_agent/agent.py (ingest_release_batch)",
    ),
    "update_release_batch_fields": (
        "implemented",
        "src/ops_hub/data_agent/agent.py (ON CONFLICT DO UPDATE)",
    ),
    "query_release_batches": (
        "implemented",
        "src/ops_hub/data_agent/agent.py (batch_key lookup)",
    ),
    # ── freight detail ──
    "extract_freight_detail": (
        "implemented",
        "src/ops_hub/sop/freight_detail_extractor.py",
    ),
    "enrich_release_batch": (
        "implemented",
        "src/ops_hub/sop/enrich_release_batch.py",
    ),
    # ── departure → 95306 query ──
    "build_time_window": (
        "implemented",
        "src/ops_hub/sop/shipment_query_window.py (QueryWindow.from_reference)",
    ),
    "query_95306_waybills": (
        "implemented",
        "src/ops_hub/sop/query_95306_shipments.py (query_95306_shipments_by_window)",
    ),
    # ── tracking / shipment status sync ──
    "poll_shipment_snapshots": (
        "implemented",
        "src/ops_hub/sop/shipment_status_sync.py (ShipmentStatusSync.sync)",
    ),
    # ── prototype / dry_run_only ──
    "generate_departure_excel_task": (
        "prototype",
        "scripts/gen_jljg_excel.py (hardcoded values, data-driven not complete)",
    ),
    "generate_factory_transport_json_task": (
        "prototype",
        "scripts/gen_jljg_excel.py (print-only preview, hardcoded)",
    ),
    # ── dry_run_only (simulated delivery, no real send) ──
    "send_excel_task": (
        "dry_run_only",
        "src/ops_hub/sop/delivery_result.py (simulate_delivery_result — local only)",
    ),
    "telegram_json_delivery_task": (
        "dry_run_only",
        "src/ops_hub/sop/delivery_result.py (simulate_delivery_result — local only)",
    ),
    # ── all others → default "missing" ──
}

# ── Task type mapping from runtime.task_resolver ───────────────────────
_TASK_TYPE_MAP: dict[str, str] = {
    "departure_excel": "excel_generation",
    "factory_json": "json_generation",
    "telegram_delivery": "telegram_delivery",
    "receiver_upload": "http_delivery",
}


@dataclass
class ExecutableTask:
    """One compiled SOP node with its implementation status."""

    task_id: str  # e.g. "jilin_jingang_jinzhou:departure_flow:detect_departure_message"
    project_id: str
    flow_name: str  # release_notice_flow | freight_detail_flow | departure_flow | tracking_flow
    node: str
    actions: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    next_nodes: list[str] = field(default_factory=list)
    task_type: str = ""
    executor_status: str = "missing"
    evidence_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "flow_name": self.flow_name,
            "node": self.node,
            "actions": list(self.actions),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "conditions": dict(self.conditions),
            "next_nodes": list(self.next_nodes),
            "task_type": self.task_type,
            "executor_status": self.executor_status,
            "evidence_file": self.evidence_file,
        }


@dataclass
class ExecutableTaskPlan:
    """Complete task plan for one project SOP."""

    project_id: str
    project_name: str
    flows: dict[str, list[ExecutableTask]] = field(default_factory=dict)
    task_resolver_tasks: list[ExecutableTask] = field(default_factory=list)

    @property
    def all_tasks(self) -> list[ExecutableTask]:
        result: list[ExecutableTask] = []
        for tasks in self.flows.values():
            result.extend(tasks)
        result.extend(self.task_resolver_tasks)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "flows": {
                name: [t.to_dict() for t in tasks]
                for name, tasks in self.flows.items()
            },
            "task_resolver_tasks": [t.to_dict() for t in self.task_resolver_tasks],
        }

    def summary(self) -> dict[str, Any]:
        all_tasks = self.all_tasks
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "total_tasks": len(all_tasks),
            "implemented": sum(1 for t in all_tasks if t.executor_status == "implemented"),
            "missing": sum(1 for t in all_tasks if t.executor_status == "missing"),
            "prototype": sum(1 for t in all_tasks if t.executor_status == "prototype"),
            "dry_run_only": sum(1 for t in all_tasks if t.executor_status == "dry_run_only"),
        }


def _action_status(action: str) -> tuple[str, str]:
    """Return (executor_status, evidence_file) for an action name."""
    if action in _EXECUTOR_STATUS:
        return _EXECUTOR_STATUS[action]
    return ("missing", "")


def _compile_flow_steps(
    project_id: str,
    flow_name: str,
    steps: list[dict[str, Any]],
) -> list[ExecutableTask]:
    """Compile a single flow's steps into ExecutableTasks."""
    tasks: list[ExecutableTask] = []
    for index, step in enumerate(steps):
        node = step.get("node", f"unknown_node_{index}")
        actions = step.get("action", []) or []
        if isinstance(actions, str):
            actions = [actions]
        outputs = step.get("outputs", []) or []
        if isinstance(outputs, str):
            outputs = [outputs]
        conditions = step.get("conditions", {}) or {}
        if not isinstance(conditions, dict):
            conditions = {}

        # next nodes
        next_nodes: list[str] = []
        if "next" in step:
            next_list = step["next"]
            if isinstance(next_list, list):
                next_nodes = list(next_list)
            elif isinstance(next_list, str):
                next_nodes = [next_list]
        else:
            # auto-link to next step in sequence
            if index + 1 < len(steps):
                next_nodes = [steps[index + 1].get("node", "")]

        # extract_fields become inputs
        inputs = step.get("extract_fields", []) or []
        if isinstance(inputs, str):
            inputs = [inputs]

        # Determine the "primary" action for status lookup.
        # Use the first action in the list as the canonical one.
        primary_action = actions[0] if actions else ""
        executor_status, evidence = _action_status(primary_action)

        # All secondary actions: if any is "missing", mark node as missing
        # unless the primary is already "missing".
        for act in actions[1:]:
            s, e = _action_status(act)
            if s == "missing" and executor_status != "missing":
                executor_status = "partial"
                evidence += f"; {act}: missing"
            elif s != "missing" and executor_status == "missing":
                executor_status = s
                evidence = e

        task_id = f"{project_id}:{flow_name}:{node}"

        # task_type from task_resolver mapping
        task_type = _TASK_TYPE_MAP.get(node, "")

        tasks.append(ExecutableTask(
            task_id=task_id,
            project_id=project_id,
            flow_name=flow_name,
            node=node,
            actions=actions,
            inputs=inputs,
            outputs=outputs,
            conditions=conditions,
            next_nodes=next_nodes,
            task_type=task_type,
            executor_status=executor_status,
            evidence_file=evidence,
        ))
    return tasks


def _compile_task_resolver_tasks(
    project_id: str,
    task_resolver: dict[str, Any],
) -> list[ExecutableTask]:
    """Compile runtime.task_resolver entries into ExecutableTasks."""
    tasks: list[ExecutableTask] = []
    for resolver_name, resolver_config in task_resolver.items():
        if not isinstance(resolver_config, dict):
            continue
        task_type = resolver_config.get("task_type", resolver_name)

        # Map resolver_name to a known action
        action_map = {
            "departure_excel": "generate_departure_excel_task",
            "factory_json": "generate_factory_transport_json_task",
            "telegram_delivery": "telegram_json_delivery_task",
            "receiver_upload": "dry_run_receiver_system",
        }
        primary_action = action_map.get(resolver_name, resolver_name)
        executor_status, evidence = _action_status(primary_action)

        task_id = f"{project_id}:task_resolver:{resolver_name}"
        tasks.append(ExecutableTask(
            task_id=task_id,
            project_id=project_id,
            flow_name="task_resolver",
            node=resolver_name,
            actions=[primary_action],
            inputs=[],
            outputs=[resolver_name],
            conditions={},
            next_nodes=[],
            task_type=task_type,
            executor_status=executor_status,
            evidence_file=evidence,
        ))
    return tasks


class SOPTaskCompiler:
    """Compile a SOP YAML file into an ExecutableTaskPlan."""

    def compile(self, sop_path: str | Path) -> ExecutableTaskPlan:
        """Read the SOP YAML and compile all flows into tasks."""
        path = Path(sop_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        project_id = data.get("project_id", path.stem)
        project_name = data.get("project_name", path.stem)

        flows: dict[str, list[ExecutableTask]] = {}
        yaml_flows = data.get("flows", {}) or {}

        for flow_name in ("release_notice_flow", "freight_detail_flow", "departure_flow", "tracking_flow"):
            flow_data = yaml_flows.get(flow_name)
            if not flow_data:
                continue

            if flow_name == "freight_detail_flow":
                # freight_detail_flow has a flat structure: action + extract_fields
                extract_fields = flow_data.get("extract_fields", []) or []
                flow_actions = flow_data.get("action", []) or []
                if isinstance(flow_actions, str):
                    flow_actions = [flow_actions]
                primary_action = flow_actions[0] if flow_actions else "enrich_release_batch"
                executor_status, evidence = _action_status(primary_action)

                tasks = [ExecutableTask(
                    task_id=f"{project_id}:{flow_name}:enrich_release_batch",
                    project_id=project_id,
                    flow_name=flow_name,
                    node="enrich_release_batch",
                    actions=flow_actions,
                    inputs=extract_fields,
                    outputs=["enriched_release_batch"],
                    conditions={},
                    next_nodes=[],
                    task_type="",
                    executor_status=executor_status,
                    evidence_file=evidence,
                )]
            else:
                steps = flow_data.get("steps", []) or []
                tasks = _compile_flow_steps(project_id, flow_name, steps)

            flows[flow_name] = tasks

        # task_resolver
        runtime = data.get("runtime", {}) or {}
        task_resolver_raw = runtime.get("task_resolver", {}) or {}
        resolver_tasks = _compile_task_resolver_tasks(project_id, task_resolver_raw)

        return ExecutableTaskPlan(
            project_id=project_id,
            project_name=project_name,
            flows=flows,
            task_resolver_tasks=resolver_tasks,
        )


def compile_project_sop(sop_path: str | Path) -> ExecutableTaskPlan:
    """Convenience function: compile a SOP YAML file."""
    return SOPTaskCompiler().compile(sop_path)
