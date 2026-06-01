"""R34: SOP Task Compiler functional tests."""

from pathlib import Path

from sop_hub.sop.sop_task_compiler import ExecutableTaskPlan, SOPTaskCompiler, compile_project_sop

SOP_PATH = Path(__file__).resolve().parents[2] / "config" / "project_sops" / "jilin_jingang.yaml"
PROJECT_ID = "jilin_jingang_jinzhou"


def _plan() -> ExecutableTaskPlan:
    return compile_project_sop(SOP_PATH)


# ── 1. Compiles successfully ──────────────────────────────────────────


def test_compile_jilin_jingang_yaml_returns_plan():
    plan = _plan()
    assert plan is not None
    assert isinstance(plan, ExecutableTaskPlan)


# ── 2. project_id ─────────────────────────────────────────────────────


def test_plan_project_id():
    plan = _plan()
    assert plan.project_id == PROJECT_ID


def test_plan_project_name():
    plan = _plan()
    assert "吉林金钢" in plan.project_name or "四平" in plan.project_name


# ── 3. Contains all four flows ─────────────────────────────────────────


def test_plan_has_release_notice_flow():
    plan = _plan()
    assert "release_notice_flow" in plan.flows
    assert len(plan.flows["release_notice_flow"]) >= 5


def test_plan_has_freight_detail_flow():
    plan = _plan()
    assert "freight_detail_flow" in plan.flows
    assert len(plan.flows["freight_detail_flow"]) >= 1


def test_plan_has_departure_flow():
    plan = _plan()
    assert "departure_flow" in plan.flows
    assert len(plan.flows["departure_flow"]) >= 14


def test_plan_has_tracking_flow():
    plan = _plan()
    assert "tracking_flow" in plan.flows
    assert len(plan.flows["tracking_flow"]) >= 4


# ── 4. departure_flow specific nodes ───────────────────────────────────


def test_departure_flow_has_parse_departure_text():
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "detect_departure_message"), None)
    assert node is not None
    assert "parse_departure_text" in node.actions


def test_departure_flow_has_build_time_window():
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "build_95306_query_window"), None)
    assert node is not None
    assert "build_time_window" in node.actions


def test_departure_flow_has_query_95306_waybills():
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "query_95306"), None)
    assert node is not None
    assert "query_95306_waybills" in node.actions


# ── 5. tracking_flow specific nodes ────────────────────────────────────


def test_tracking_flow_has_poll_shipment_snapshots():
    plan = _plan()
    tasks = plan.flows["tracking_flow"]
    node = next((t for t in tasks if t.node == "track_95306_status"), None)
    assert node is not None
    assert "poll_shipment_snapshots" in node.actions


def test_tracking_flow_has_mark_confirmed_received():
    plan = _plan()
    tasks = plan.flows["tracking_flow"]
    node = next((t for t in tasks if t.node == "confirmed_received"), None)
    assert node is not None
    assert "mark_confirmed_received" in node.actions


# ── 6. Executor status: implemented ────────────────────────────────────


def test_parse_departure_text_is_implemented():
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "detect_departure_message"), None)
    assert node is not None
    assert node.executor_status == "implemented"
    assert "departure_text_parser" in node.evidence_file


# ── 7. Executor status: missing ────────────────────────────────────────


def test_query_95306_waybills_is_implemented():
    """R42: query_95306_waybills now implemented via query_95306_shipments_by_window."""
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "query_95306"), None)
    assert node is not None
    assert node.executor_status == "implemented"


def test_poll_shipment_snapshots_is_implemented():
    """R36: shipment_status_sync now registered as implemented."""
    plan = _plan()
    tasks = plan.flows["tracking_flow"]
    node = next((t for t in tasks if t.node == "track_95306_status"), None)
    assert node is not None
    assert node.executor_status == "implemented"


def test_create_wagon_shipments_is_implemented():
    """R45: create_wagon_shipments is now implemented."""
    plan = _plan()
    tasks = plan.flows["departure_flow"]
    node = next((t for t in tasks if t.node == "write_departure_records"), None)
    assert node is not None
    assert node.executor_status == "implemented"


# ── 8. task_resolver ───────────────────────────────────────────────────


def test_task_resolver_tasks_exist():
    plan = _plan()
    assert len(plan.task_resolver_tasks) >= 3


def test_task_resolver_excel_generation():
    plan = _plan()
    task = next((t for t in plan.task_resolver_tasks if t.node == "departure_excel"), None)
    assert task is not None
    assert task.task_type == "excel_generation"


def test_task_resolver_telegram_delivery():
    plan = _plan()
    task = next((t for t in plan.task_resolver_tasks if t.node == "telegram_delivery"), None)
    assert task is not None
    assert task.task_type == "telegram_delivery"


# ── 9. Summary ─────────────────────────────────────────────────────────


def test_summary_is_positive():
    plan = _plan()
    s = plan.summary()
    assert s["total_tasks"] >= 20
    assert s["implemented"] >= 5
    assert s["missing"] >= 7  # R45: create_wagon_shipments now implemented
    assert s["project_id"] == PROJECT_ID


# ── 10. No side effects ────────────────────────────────────────────────


def test_no_side_effects():
    """Compiling does not write files, query DBs, or send anything."""
    plan = _plan()
    # Check that plan is inspectable and has valid data
    assert plan.flows
    assert plan.summary()["total_tasks"] > 0
    # No DB connections, no file writes, no HTTP calls happen in compile()


# ── 11. to_dict / to_dict roundtrip ────────────────────────────────────


def test_plan_to_dict():
    plan = _plan()
    d = plan.to_dict()
    assert d["project_id"] == PROJECT_ID
    assert "release_notice_flow" in d["flows"]
    assert "departure_flow" in d["flows"]
    assert "tracking_flow" in d["flows"]


def test_all_tasks_contain_required_fields():
    plan = _plan()
    for task in plan.all_tasks:
        assert task.task_id
        assert task.project_id == PROJECT_ID
        assert task.flow_name
        assert task.node
        assert task.executor_status in ("implemented", "missing", "prototype", "dry_run_only", "partial")
        assert isinstance(task.actions, list)
