"""SOP task execution registry and message-to-task trace.

Reads ExecutableTaskPlan from SOPTaskCompiler, matches MessageEvent
against known flow patterns, and generates TaskExecutionTrace showing:
- which flow/node matched
- which tasks are implemented/missing
- next missing task blocking execution
- overall status

This module stays local-only:
- does not execute tasks;
- does not write DB, query 95306, generate Excel, or send reports;
- optional trace file writes to runtime/task_traces/ (gitignored).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sop_hub.sop.departure_text_parser import parse_departure_text
from sop_hub.sop.monitoring_plan_matcher import MessageEvent
from sop_hub.sop.sop_task_compiler import ExecutableTask, ExecutableTaskPlan


# ── Freight detail text patterns (from jilin_jingang.yaml) ────────────
_FREIGHT_DETAIL_PATTERNS = [
    "标识号", "订单标识", "订单号", "合同号", "合同",
    "货名", "品名", "详细货名", "矿种", "批次",
]


@dataclass
class TaskExecutorAction:
    """Single task action with its implementation status."""

    action: str
    executor_status: str  # implemented | missing | prototype | dry_run_only
    evidence_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "executor_status": self.executor_status,
            "evidence_file": self.evidence_file,
        }


@dataclass
class TaskExecutionTrace:
    """Full execution trace for one message against a task plan.

    Status values:
      - "ready"                — all tasks implemented, can execute
      - "blocked_missing_executor" — at least one task is missing
      - "no_matching_flow"     — message didn't match any flow
      - "dry_run_only"         — tasks exist but only simulated delivery
    """

    trace_id: str
    message_id: str
    group_id: str
    project_id: str
    received_at: str = ""
    trace_at: str = ""

    matched_flow: str = ""
    matched_node: str = ""
    message_text: str = ""

    generated_tasks: int = 0
    executable_tasks: int = 0
    missing_tasks: int = 0
    next_missing_task: str = ""

    status: str = "no_matching_flow"
    reason: str = ""

    actions: list[TaskExecutorAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "received_at": self.received_at,
            "trace_at": self.trace_at,
            "matched_flow": self.matched_flow,
            "matched_node": self.matched_node,
            "message_text": self.message_text[:200],
            "generated_tasks": self.generated_tasks,
            "executable_tasks": self.executable_tasks,
            "missing_tasks": self.missing_tasks,
            "next_missing_task": self.next_missing_task,
            "status": self.status,
            "reason": self.reason,
            "actions": [a.to_dict() for a in self.actions],
        }


class TaskExecutionRegistry:
    """Match MessageEvents against ExecutableTaskPlan and produce traces."""

    def __init__(self, plan: ExecutableTaskPlan, *, trace_dir: str | Path = ""):
        self.plan = plan
        self.trace_dir = Path(trace_dir) if trace_dir else None

    # ── message → flow matching ───────────────────────────────────────

    def _match_departure_flow(self, event: MessageEvent) -> tuple[str, str] | None:
        """Return (flow_name, first_node) if message is departure text."""
        candidate = parse_departure_text(event)
        if candidate.status in ("complete", "incomplete"):
            return ("departure_flow", "detect_departure_message")
        return None

    def _match_freight_detail_flow(self, event: MessageEvent) -> tuple[str, str] | None:
        """Return (flow_name, first_node) if message contains freight detail keywords."""
        text = event.text or ""
        if any(pattern in text for pattern in _FREIGHT_DETAIL_PATTERNS):
            # Also ensure it's not a departure text (avoid double-match)
            if parse_departure_text(event).status == "no_match":
                return ("freight_detail_flow", "enrich_release_batch")
        return None

    def _match_flow(self, event: MessageEvent) -> tuple[str, str] | None:
        """Route a message to (flow_name, first_matched_node)."""
        # Check departure first (more specific)
        flow = self._match_departure_flow(event)
        if flow:
            return flow
        # Then freight detail
        flow = self._match_freight_detail_flow(event)
        if flow:
            return flow
        return None

    # ── trace generation ──────────────────────────────────────────────

    def generate_trace(self, event: MessageEvent, *, write: bool = False) -> TaskExecutionTrace:
        """Generate a TaskExecutionTrace for a message event.

        Args:
          event: the message to trace.
          write: if True, write trace JSON to trace_dir.

        Returns:
          TaskExecutionTrace with status and actions.
        """
        from sop_hub.utils.time import now_iso_beijing_compact
        now = now_iso_beijing_compact()
        trace_id = f"trace_{event.message_id}_{now.replace(':', '').replace('-', '')[:15]}"

        flow = self._match_flow(event)
        if flow is None:
            return self._no_match_trace(
                trace_id=trace_id,
                event=event,
                now=now,
            )

        flow_name, first_node = flow
        return self._build_flow_trace(
            trace_id=trace_id,
            event=event,
            flow_name=flow_name,
            first_node=first_node,
            now=now,
            write=write,
        )

    def _no_match_trace(self, *, trace_id: str, event: MessageEvent, now: str) -> TaskExecutionTrace:
        return TaskExecutionTrace(
            trace_id=trace_id,
            message_id=event.message_id,
            group_id=event.group_id or "",
            project_id=self.plan.project_id,
            received_at=event.received_at or "",
            trace_at=now,
            matched_flow="",
            matched_node="",
            message_text=event.text or "",
            generated_tasks=0,
            executable_tasks=0,
            missing_tasks=0,
            next_missing_task="",
            status="no_matching_flow",
            reason="message did not match any SOP flow",
            actions=[],
        )

    def _build_flow_trace(
        self,
        *,
        trace_id: str,
        event: MessageEvent,
        flow_name: str,
        first_node: str,
        now: str,
        write: bool = False,
    ) -> TaskExecutionTrace:
        # Collect all tasks for the matched flow
        flow_tasks = self.plan.flows.get(flow_name, [])
        generated_tasks = len(flow_tasks)

        actions: list[TaskExecutorAction] = []
        for task in flow_tasks:
            for action in task.actions:
                actions.append(TaskExecutorAction(
                    action=action,
                    executor_status=task.executor_status,
                    evidence_file=task.evidence_file,
                ))

        executable = sum(1 for a in actions if a.executor_status == "implemented")
        missing_actions = [a for a in actions if a.executor_status == "missing"]
        missing_count = len(missing_actions)

        # Find the first missing action (sequential walk through tasks)
        next_missing = ""
        for task in flow_tasks:
            if task.executor_status == "missing":
                next_missing = task.node
            elif task.executor_status == "implemented":
                # skip — continue
                if next_missing:
                    # already found a missing task, keep it
                    pass
            # prototype/dry_run_only — treat as existing but not production-grade
            elif task.executor_status in ("prototype", "dry_run_only"):
                if not next_missing:
                    # current task exists but is not production-grade
                    # keep looking for the next truly missing task
                    pass
        # Actually, the first missing task should be the first task with status "missing"
        for task in flow_tasks:
            if task.executor_status == "missing":
                next_missing = task.node
                break

        # Derive status
        if missing_count > 0:
            status = "blocked_missing_executor"
            reason = f"flow={flow_name}, {missing_count} missing executor(s), first missing={next_missing}"
        elif executable == generated_tasks:
            status = "ready"
            reason = f"flow={flow_name}, all {generated_tasks} executors implemented"
        elif all(
            t.executor_status in ("dry_run_only", "prototype")
            for t in flow_tasks
        ):
            status = "dry_run_only"
            reason = f"flow={flow_name}, all executors are dry_run_only or prototype"
        else:
            status = "ready"
            reason = f"flow={flow_name}, {executable}/{generated_tasks} executors available"

        trace = TaskExecutionTrace(
            trace_id=trace_id,
            message_id=event.message_id,
            group_id=event.group_id or "",
            project_id=self.plan.project_id,
            received_at=event.received_at or "",
            trace_at=now,
            matched_flow=flow_name,
            matched_node=first_node,
            message_text=event.text or "",
            generated_tasks=generated_tasks,
            executable_tasks=executable,
            missing_tasks=missing_count,
            next_missing_task=next_missing,
            status=status,
            reason=reason,
            actions=actions,
        )

        if write and self.trace_dir:
            self._write_trace(trace)

        return trace

    def _write_trace(self, trace: TaskExecutionTrace) -> Path | None:
        """Write trace JSON to trace_dir."""
        if self.trace_dir is None:
            return None
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{trace.trace_id}.json"
        path.write_text(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def create_registry_for_project(
    sop_yaml_path: str | Path,
    *,
    trace_dir: str | Path = "",
) -> TaskExecutionRegistry:
    """Convenience: compile a SOP and create a registry."""
    from sop_hub.sop.sop_task_compiler import compile_project_sop

    plan = compile_project_sop(sop_yaml_path)
    return TaskExecutionRegistry(plan=plan, trace_dir=trace_dir)
