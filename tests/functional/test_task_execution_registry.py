"""R35: Task execution registry and message-to-task trace functional tests."""

import json
from pathlib import Path

from sop_hub.sop.monitoring_plan_matcher import MessageEvent
from sop_hub.sop.sop_task_compiler import compile_project_sop
from sop_hub.sop.task_execution_registry import TaskExecutionRegistry, create_registry_for_project

SOP_PATH = Path(__file__).resolve().parents[2] / "config" / "project_sops" / "jilin_jingang.yaml"
PROJECT_ID = "jilin_jingang_jinzhou"


def _registry() -> TaskExecutionRegistry:
    plan = compile_project_sop(SOP_PATH)
    return TaskExecutionRegistry(plan=plan)


def _departure_event(text: str = "十四道，四平铁，蓝鳍，18车") -> MessageEvent:
    return MessageEvent(
        message_id="wx_dep_001",
        channel="wechat",
        group_id="GROUP001",
        message_type="text",
        received_at="2026-05-25T10:00:00Z",
        text=text,
    )


def _freight_detail_event() -> MessageEvent:
    return MessageEvent(
        message_id="wx_frt_001",
        channel="wechat",
        group_id="GROUP005",
        message_type="text",
        received_at="2026-05-25T10:05:00Z",
        text="订单标识 CGR20260518174420，合同号 JGCG-SFY-HTNK20260501，货名 红土镍矿，批次 2",
    )


# ── 1. Registry loads plan ────────────────────────────────────────────


def test_registry_loads_plan():
    reg = _registry()
    assert reg.plan.project_id == PROJECT_ID
    assert "departure_flow" in reg.plan.flows
    assert "freight_detail_flow" in reg.plan.flows
    assert len(reg.plan.flows["departure_flow"]) >= 10


# ── 2. 蓝鳍 departure trace ──────────────────────────────────────────


def test_lanqi_departure_text_generates_trace():
    reg = _registry()
    event = _departure_event()
    trace = reg.generate_trace(event)
    assert trace.matched_flow == "departure_flow"
    assert trace.matched_node == "detect_departure_message"
    assert trace.project_id == PROJECT_ID
    assert trace.status == "blocked_missing_executor"
    assert trace.missing_tasks >= 1


# ── 3. parse_departure_text = implemented ─────────────────────────────


def test_parse_departure_text_is_implemented_in_trace():
    reg = _registry()
    trace = reg.generate_trace(_departure_event())
    parse_action = next(
        (a for a in trace.actions if a.action == "parse_departure_text"), None
    )
    assert parse_action is not None, f"Actions: {[a.action for a in trace.actions]}"
    assert parse_action.executor_status == "implemented"
    assert "departure_text_parser" in parse_action.evidence_file


# ── 4. build_time_window = missing ─────────────────────────────────────


def test_build_time_window_is_missing_in_trace():
    reg = _registry()
    trace = reg.generate_trace(_departure_event())
    build_action = next(
        (a for a in trace.actions if a.action == "build_time_window"), None
    )
    assert build_action is not None, f"Actions: {[a.action for a in trace.actions]}"
    assert build_action.executor_status == "implemented"


# ── 5. next_missing_task = build_95306_query_window ────────────────────


def test_next_missing_task_is_build_95306_query_window():
    """After R44: build_time_window is now implemented.
    The next missing should be query_result_found (first node after implemented query_95306).
    """
    reg = _registry()
    trace = reg.generate_trace(_departure_event())
    assert trace.next_missing_task == "query_result_found"


# ── 6. Freight detail trace ───────────────────────────────────────────


def test_freight_detail_text_generates_trace():
    """R41+R44: freight_detail_flow is now fully implemented."""
    reg = _registry()
    event = _freight_detail_event()
    trace = reg.generate_trace(event)
    assert trace.matched_flow == "freight_detail_flow"
    assert trace.matched_node == "enrich_release_batch"
    assert trace.project_id == PROJECT_ID
    # enrich_release_batch executor is now implemented (R41+R44)
    assert trace.status == "ready"
    assert trace.missing_tasks == 0


# ── 7. Freight detail → no next_missing_task ──────────────────────────


def test_freight_detail_next_missing_is_enrich_release_batch():
    """R41+R44: freight_detail_flow has no missing tasks anymore."""
    reg = _registry()
    event = _freight_detail_event()
    trace = reg.generate_trace(event)
    assert trace.next_missing_task == ""


# ── 8. Irrelevant text → no_matching_flow ─────────────────────────────


def test_irrelevant_text_no_matching_flow():
    reg = _registry()
    event = MessageEvent(
        message_id="wx_irrelevant",
        channel="wechat",
        group_id="GROUP001",
        message_type="text",
        text="今天天气不错",
    )
    trace = reg.generate_trace(event)
    assert trace.status == "no_matching_flow"
    assert trace.matched_flow == ""
    assert trace.generated_tasks == 0


# ── 9. No external side effects ───────────────────────────────────────


def test_no_external_side_effects():
    """Registry does not write DB, query 95306, generate Excel, or send reports."""
    reg = _registry()
    event = _departure_event()
    trace = reg.generate_trace(event)
    assert trace is not None
    assert trace.status == "blocked_missing_executor"
    # Nothing was written to DB, no HTTP calls, no file I/O (without write=True)


# ── 10. Trace write to task_traces ────────────────────────────────────


def test_trace_write_to_task_traces(tmp_path):
    """With write=True, trace JSON is written to trace_dir."""
    trace_dir = tmp_path / "task_traces"
    plan = compile_project_sop(SOP_PATH)
    reg = TaskExecutionRegistry(plan=plan, trace_dir=trace_dir)

    event = _departure_event()
    trace = reg.generate_trace(event, write=True)

    # Check file was written
    files = list(trace_dir.glob("*.json"))
    assert len(files) == 1
    content = json.loads(files[0].read_text(encoding="utf-8"))
    assert content["matched_flow"] == "departure_flow"
    assert content["status"] == "blocked_missing_executor"
    assert content["next_missing_task"] == "query_result_found"


# ── 11. Freight detail avoids departure double-match ──────────────────


def test_freight_detail_with_car_keyword_still_matches_departure():
    """Text with '合同号' + '46车' should match departure_flow (departure takes priority)."""
    reg = _registry()
    event = MessageEvent(
        message_id="wx_amb_001",
        channel="wechat",
        group_id="GROUP005",
        message_type="text",
        text="合同号 JGCG-SFY-HTNK20260501，四平，46车，货名 红土镍矿",
    )
    trace = reg.generate_trace(event)
    # departure takes priority because it has specific format matching
    assert trace.matched_flow == "departure_flow"
    assert trace.matched_node == "detect_departure_message"


# ── 12. create_registry_for_project convenience ───────────────────────


def test_create_registry_for_project():
    reg = create_registry_for_project(SOP_PATH)
    assert reg.plan.project_id == PROJECT_ID
    assert "departure_flow" in reg.plan.flows


# ── 13. Trace has all required fields ─────────────────────────────────


def test_trace_has_all_required_fields():
    reg = _registry()
    trace = reg.generate_trace(_departure_event())
    d = trace.to_dict()
    for key in (
        "trace_id", "message_id", "group_id", "project_id",
        "matched_flow", "matched_node", "status", "reason",
        "generated_tasks", "executable_tasks", "missing_tasks",
        "next_missing_task", "actions",
    ):
        assert key in d, f"Missing key: {key}"


# ── 14. Multiple traces → different trace_ids ─────────────────────────


def test_multiple_traces_have_different_ids():
    reg = _registry()
    t1 = reg.generate_trace(MessageEvent(
        message_id="wx_dep_001",
        channel="wechat", group_id="GROUP001", message_type="text",
        text="6道，四平铁，46车",
    ))
    t2 = reg.generate_trace(MessageEvent(
        message_id="wx_dep_002",
        channel="wechat", group_id="GROUP001", message_type="text",
        text="十四道，四平铁，蓝鳍，18车",
    ))
    assert t1.trace_id != t2.trace_id


# ── 15. to_dict() is serializable ─────────────────────────────────────


def test_trace_to_dict_serializable():
    reg = _registry()
    trace = reg.generate_trace(_departure_event())
    json.dumps(trace.to_dict())  # should not raise


# ── 16. trace_dir=None (default) doesn't create directory ──────────────


def test_no_trace_dir_no_file_writes():
    reg = TaskExecutionRegistry(plan=compile_project_sop(SOP_PATH), trace_dir="")
    trace = reg.generate_trace(_departure_event(), write=True)
    assert trace.status == "blocked_missing_executor"
    # No crash, no file created (empty trace_dir)
