from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import dashboard_snapshot as snapshot  # noqa: E402
import cli_dashboard as dashboard  # noqa: E402
from sop_hub.sop import shipped_weight  # noqa: E402


def test_snapshot_is_structured_complete_and_does_not_call_projection_refresher(monkeypatch):
    batch = {
        "id": "lot-1",
        "ship_name": "测试船",
        "cargo_name": "铁矿粉",
        "cargo_product_name": "混合粉",
        "batch_sequence": "lot01",
        "batch_date": "2026-09-18",
        "destination_station": "四平",
        "batch_quantity": 100.0,
        "shipped_weight_tons": 40.0,
        "remaining_weight_tons": None,
        "box_count": 2,
        "today_box": 1,
        "dispatch_status": "loading",
        "order_identifier": "ORDER-1",
        "updated_at": "2026-09-18 10:00:00",
    }
    monkeypatch.setattr(
        snapshot.dashboard,
        "query_projects_with_batches",
        lambda: {"jilin_jingang_jinzhou": [batch]},
    )
    monkeypatch.setattr(snapshot.dashboard, "query_reserved_wagon_counts", lambda: {"jilin_jingang_jinzhou": 3})
    monkeypatch.setattr(snapshot.dashboard, "_container_business_projects", lambda: {"jilin_jingang_jinzhou"})
    monkeypatch.setattr(snapshot.dashboard, "_project_lifecycle_modes", lambda: {"jilin_jingang_jinzhou": "full_track_to_received"})
    monkeypatch.setattr(snapshot.dashboard, "query_pending_candidate_counts", lambda: {"pending_review": 2})
    monkeypatch.setattr(snapshot.dashboard, "query_pending_release_notice_count", lambda: 1)
    monkeypatch.setattr(snapshot.dashboard, "_railway_plan_day_window", lambda: ("2026-09-17 18:00:00", "2026-09-18 18:00:00"))
    monkeypatch.setattr(snapshot.dashboard, "list_daemons", lambda: [{"label": "text_watch", "ok": True}])
    monkeypatch.setattr(snapshot.dashboard, "refresh_active_shipped_weights", lambda: pytest.fail("snapshot must be read-only"))
    monkeypatch.setattr(snapshot.jiusan, "build_snapshot", lambda: {"status": "ok", "nodes": []})

    result = snapshot.build_snapshot()

    assert result["schema_version"] == "freight-dashboard.v1"
    assert result["source_as_of"]["railway_plan_day_start"] == "2026-09-17 18:00:00"
    assert result["pending_items"] == {
        "candidate_counts": {"pending_review": 2},
        "candidate_total": 2,
        "pending_release_notices": 1,
    }
    project = result["projects"][0]
    assert project["reserved_wagons"] == 3
    assert project["unit"] == "containers"
    assert project["batches"] == [{
        "id": "lot-1", "ship_name": "测试船", "cargo_name": "铁矿粉",
        "cargo_product_name": "混合粉", "batch_sequence": "lot01",
        "batch_date": "2026-09-18", "destination_station": "四平",
        "planned_tons": 100.0, "shipped_tons": 40.0, "remaining_tons": 60.0,
        "unit": "containers", "unit_count": 2, "today_unit_count": 1,
        "dispatch_status": "loading", "plan_number": "ORDER-1",
        "updated_at": "2026-09-18 10:00:00",
    }]


def test_snapshot_keeps_unknown_project_and_untruncated_batches(monkeypatch):
    batches = [{"id": f"lot-{index}", "batch_sequence": f"lot{index:02d}"} for index in range(10)]
    monkeypatch.setattr(snapshot.dashboard, "query_projects_with_batches", lambda: {"custom": batches})
    monkeypatch.setattr(snapshot.dashboard, "query_reserved_wagon_counts", lambda: {})
    monkeypatch.setattr(snapshot.dashboard, "_container_business_projects", lambda: set())
    monkeypatch.setattr(snapshot.dashboard, "_project_lifecycle_modes", lambda: {})
    monkeypatch.setattr(snapshot.dashboard, "query_pending_candidate_counts", lambda: {})
    monkeypatch.setattr(snapshot.dashboard, "query_pending_release_notice_count", lambda: 0)
    monkeypatch.setattr(snapshot.dashboard, "_railway_plan_day_window", lambda: ("a", "b"))
    monkeypatch.setattr(snapshot.dashboard, "list_daemons", lambda: [])
    monkeypatch.setattr(snapshot.jiusan, "build_snapshot", lambda: {"status": "unavailable"})

    result = snapshot.build_snapshot()

    custom = next(project for project in result["projects"] if project["id"] == "custom")
    assert len(custom["batches"]) == 10


def test_encoder_is_json_and_enforces_size_limit(monkeypatch):
    encoded = snapshot.encode_snapshot({"schema_version": snapshot.SCHEMA_VERSION})
    assert json.loads(encoded) == {"schema_version": snapshot.SCHEMA_VERSION}

    monkeypatch.setattr(snapshot, "MAX_OUTPUT_BYTES", 5)
    with pytest.raises(ValueError, match="byte limit"):
        snapshot.encode_snapshot({"text": "过长"})


def test_active_weight_projection_is_sop_maintenance_not_snapshot_read(tmp_path, monkeypatch):
    db = tmp_path / "sop.sqlite3"
    conn = __import__("sqlite3").connect(db)
    conn.execute("CREATE TABLE release_batches (id TEXT, dispatch_status TEXT)")
    conn.executemany(
        "INSERT INTO release_batches VALUES (?, ?)",
        [("active-a", "loading"), ("active-b", "tracking"), ("done", "closed")],
    )
    conn.commit()
    conn.close()

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        shipped_weight,
        "compute_for_release_batch",
        lambda batch_id, **kwargs: calls.append((batch_id, kwargs["write_basis"])) or {"ok": True},
    )

    result = shipped_weight.refresh_active_weight_projections(db_path=db)

    assert calls == [("active-a", False), ("active-b", False)]
    assert result == {"attempted": 2, "updated": 2, "skipped": 0, "errors": []}

    delegated: list[Path] = []
    monkeypatch.setattr(
        shipped_weight,
        "refresh_active_weight_projections",
        lambda *, db_path: delegated.append(db_path),
    )
    dashboard._last_sw_recompute = 0
    dashboard.refresh_active_shipped_weights(throttle_s=0)
    assert delegated == [dashboard.DB_PATH]
