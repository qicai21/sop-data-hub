"""R44: executor registry status tests — verify _EXECUTOR_STATUS accuracy.

Tests ensure the executor registry correctly reflects implemented vs missing
executors after R40-R42 implementations.
"""

from __future__ import annotations

import json

from ops_hub.sop.sop_task_compiler import (
    _EXECUTOR_STATUS,
    _action_status,
    compile_project_sop,
)
from ops_hub.sop.task_execution_registry import (
    TaskExecutionRegistry,
    create_registry_for_project,
)
from ops_hub.sop.monitoring_plan_matcher import MessageEvent


# ── Registry-level tests ────────────────────────────────────────────────

def test_query_95306_waybills_is_implemented():
    """R42: query_95306_shipments_by_window should be registered as implemented."""
    status, evidence = _action_status("query_95306_waybills")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "query_95306_shipments" in evidence


def test_enrich_release_batch_is_implemented():
    """R41: enrich_release_batch executor should be registered as implemented."""
    status, evidence = _action_status("enrich_release_batch")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "enrich_release_batch" in evidence


def test_extract_freight_detail_is_implemented():
    """R40: freight_detail_extractor should be registered as implemented."""
    status, evidence = _action_status("extract_freight_detail")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "freight_detail_extractor" in evidence


def test_poll_shipment_snapshots_is_implemented():
    """R36: shipment_status_sync should be registered as implemented."""
    status, evidence = _action_status("poll_shipment_snapshots")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "shipment_status_sync" in evidence


def test_build_time_window_is_implemented():
    """R42: QueryWindow should be registered as implemented."""
    status, evidence = _action_status("build_time_window")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "shipment_query_window" in evidence


# ── Still-missing tests ─────────────────────────────────────────────────

def test_create_wagon_shipments_is_implemented():
    """R45: create_wagon_shipments now implemented."""
    status, evidence = _action_status("create_wagon_shipments")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "create_wagon_shipments" in evidence


def test_bind_wagons_to_release_batch_is_implemented():
    """R45: bind_wagons_to_release_batch now implemented."""
    status, evidence = _action_status("bind_wagons_to_release_batch")
    assert status == "implemented", f"Expected 'implemented', got '{status}'"
    assert "create_wagon_shipments" in evidence


def test_report_sender_adapter_still_missing():
    """send_excel_task should remain dry_run_only (not implemented)."""
    status, _ = _action_status("send_excel_task")
    assert status in ("dry_run_only",), (
        f"send_excel_task should be dry_run_only, got '{status}'"
    )


# ── Prototype not mislabeled ────────────────────────────────────────────

def test_prototype_not_implemented():
    """generate_departure_excel_task is prototype — must not be 'implemented'."""
    status, _ = _action_status("generate_departure_excel_task")
    assert status == "prototype", (
        f"generate_departure_excel_task should be prototype, got '{status}'"
    )


# ── Task plan-level tests ──────────────────────────────────────────────

def test_jilin_jingang_plan_implemented_count_increased():
    """After R44 registry refresh, implemented count should be >= 9."""
    plan = compile_project_sop("config/project_sops/jilin_jingang.yaml")
    s = plan.summary()
    assert s["implemented"] >= 9, (
        f"Expected >= 9 implemented tasks, got {s['implemented']}. "
        f"Summary: {s}"
    )
    assert s["missing"] <= 12, (
        f"Expected <= 12 missing tasks, got {s['missing']}. "
        f"Summary: {s}"
    )


def test_freight_detail_flow_is_implemented():
    """The freight_detail_flow (enrich_release_batch node) must be implemented."""
    plan = compile_project_sop("config/project_sops/jilin_jingang.yaml")
    tasks = plan.flows.get("freight_detail_flow", [])
    assert len(tasks) >= 1, "freight_detail_flow should have at least 1 task"
    for t in tasks:
        assert t.executor_status == "implemented", (
            f"freight_detail_flow node '{t.node}' should be implemented, "
            f"got '{t.executor_status}'"
        )


def test_departure_flow_query_nodes_are_implemented():
    """build_95306_query_window and query_95306 must be implemented."""
    plan = compile_project_sop("config/project_sops/jilin_jingang.yaml")
    tasks = plan.flows.get("departure_flow", [])
    task_map = {t.node: t for t in tasks}

    build = task_map.get("build_95306_query_window")
    assert build is not None, "build_95306_query_window node missing"
    assert build.executor_status == "implemented", (
        f"build_95306_query_window should be implemented, got '{build.executor_status}'"
    )

    query = task_map.get("query_95306")
    assert query is not None, "query_95306 node missing"
    assert query.executor_status == "implemented", (
        f"query_95306 should be implemented, got '{query.executor_status}'"
    )


def test_write_departure_records_is_implemented():
    """R45: write_departure_records (create_wagon_shipments) now implemented."""
    plan = compile_project_sop("config/project_sops/jilin_jingang.yaml")
    tasks = plan.flows.get("departure_flow", [])
    task_map = {t.node: t for t in tasks}

    write = task_map.get("write_departure_records")
    assert write is not None, "write_departure_records node missing"
    assert write.executor_status == "implemented", (
        f"write_departure_records should be implemented, got '{write.executor_status}'"
    )


# ── TaskExecutionTrace tests ────────────────────────────────────────────

def _make_lanqi_event() -> MessageEvent:
    return MessageEvent(
        message_id="wx_test_001",
        channel="wechat",
        group_id="GROUP001",
        message_type="text",
        received_at="2026-05-24T07:16:00Z",
        text='煤六   四平铁\u201c蓝鳍\u201d18节',
    )


def test_lanqi_trace_next_missing_is_create_wagon_shipments():
    """For 蓝鳍 departure text, next_missing_task should be a node AFTER
    the now-implemented query_95306, specifically something in the
    create_wagon_shipments chain (not an old-implemented executor)."""
    registry = create_registry_for_project("config/project_sops/jilin_jingang.yaml")
    event = _make_lanqi_event()
    trace = registry.generate_trace(event, write=False)

    # The next_missing should reference a truly missing node, not
    # build_time_window or query_95306_waybills (now implemented).
    assert trace.next_missing_task != "", "Should have a next_missing_task"
    assert trace.next_missing_task not in (
        "build_95306_query_window",
        "query_95306",
        "detect_departure_message",
    ), (
        f"next_missing_task={trace.next_missing_task} should NOT be a "
        f"now-implemented executor"
    )
    # The first missing should be query_result_found or extract_wagons
    # (the next in sequence after the implemented query_95306)
    assert trace.next_missing_task in (
        "query_result_found",
        "extract_wagons_and_waybills",
        "deduplicate_departure_records",
        "match_release_batch",
        "write_departure_records",
    ), f"Unexpected next_missing_task: {trace.next_missing_task}"


def test_lanqi_trace_implemented_executors():
    """蓝鳍 trace should show parse_departure_text, build_time_window,
    query_95306_waybills as implemented actions."""
    registry = create_registry_for_project("config/project_sops/jilin_jingang.yaml")
    event = _make_lanqi_event()
    trace = registry.generate_trace(event, write=False)

    implemented_actions = {
        a.action for a in trace.actions if a.executor_status == "implemented"
    }
    assert "parse_departure_text" in implemented_actions
    assert "build_time_window" in implemented_actions
    assert "query_95306_waybills" in implemented_actions


# ── live_service --status test ─────────────────────────────────────────

def test_live_service_status_reflects_registry_update():
    """live_service --status should show at least 9 implemented for jilin_jingang."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "scripts/run_live_service.py", "--status",
        ],
        capture_output=True, text=True,
        timeout=15,
    )
    # run_live_service.py needs cwd to be the project root
    # but that's handled by the test runner

    assert result.returncode == 0, f"Status command failed: {result.stderr}"
    data = json.loads(result.stdout)
    task_runtime = data.get("sop_task_runtime", {})
    plans = task_runtime.get("plans", [])
    jljg = next((p for p in plans if p["project_id"] == "jilin_jingang_jinzhou"), None)
    assert jljg is not None, "jilin_jingang plan missing from status"
    assert jljg["implemented"] >= 9, (
        f"Expected >= 9 implemented in live_service status, got {jljg['implemented']}"
    )
    assert jljg["missing"] <= 12, (
        f"Expected <= 12 missing, got {jljg['missing']}"
    )
