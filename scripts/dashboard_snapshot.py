#!/usr/bin/env python3
"""Versioned, read-only data contract for freight dashboard consumers.

This module deliberately has no ANSI, HTML, Slack, network, or write path.
The future Slack dashboard invokes it through a short-lived local subprocess,
so the gateway neither imports SOP internals nor owns business SQL.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import cli_dashboard as dashboard  # noqa: E402
import jiusan_cycle_board as jiusan  # noqa: E402
from sop_hub.utils.time import now_iso_beijing  # noqa: E402


SCHEMA_VERSION = "freight-dashboard.v1"
MAX_OUTPUT_BYTES = 1_000_000


def _number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _batch_snapshot(project_id: str, batch: dict[str, Any], is_container: bool) -> dict[str, Any]:
    planned = _number(batch.get("batch_quantity"))
    shipped = _number(batch.get("shipped_weight_tons"))
    remaining = _number(batch.get("remaining_weight_tons"))
    if remaining is None and planned is not None:
        remaining = planned - (shipped or 0)
    unit_count = int(
        (batch.get("box_count") if is_container else batch.get("actual_wagon_count")) or 0
    )
    today_unit_count = int(
        (batch.get("today_box") if is_container else batch.get("today_wagon")) or 0
    )
    return {
        "id": str(batch.get("id") or ""),
        "ship_name": str(batch.get("ship_name") or ""),
        "cargo_name": str(batch.get("cargo_name") or ""),
        "cargo_product_name": str(batch.get("cargo_product_name") or ""),
        "batch_sequence": str(batch.get("batch_sequence") or ""),
        "batch_date": str(batch.get("batch_date") or ""),
        "destination_station": str(batch.get("destination_station") or ""),
        "planned_tons": planned,
        "shipped_tons": shipped,
        "remaining_tons": remaining,
        "unit": "containers" if is_container else "wagons",
        "unit_count": unit_count,
        "today_unit_count": today_unit_count,
        "dispatch_status": str(batch.get("dispatch_status") or ""),
        "plan_number": dashboard._plan_no_for(project_id, batch),
        "updated_at": str(batch.get("updated_at") or ""),
    }


def build_snapshot() -> dict[str, Any]:
    """Build a complete snapshot without calling any projection refresher."""
    by_project = dashboard.query_projects_with_batches()
    reserved = dashboard.query_reserved_wagon_counts()
    container_projects = dashboard._container_business_projects()
    lifecycle_modes = dashboard._project_lifecycle_modes()
    ordered_ids = [project for project in dashboard.PROJECT_DISPLAY if project in by_project]
    if "jiusan" not in ordered_ids:
        ordered_ids.append("jiusan")
    ordered_ids.extend(project for project in by_project if project not in ordered_ids)

    projects: list[dict[str, Any]] = []
    for project_id in ordered_ids:
        is_container = project_id in container_projects
        batches = by_project.get(project_id, [])
        projects.append({
            "id": project_id,
            "display_name": dashboard.PROJECT_DISPLAY.get(project_id, project_id),
            "lifecycle_mode": lifecycle_modes.get(project_id, "full_track_to_received"),
            "unit": "containers" if is_container else "wagons",
            "reserved_wagons": reserved.get(project_id),
            "batches": [_batch_snapshot(project_id, batch, is_container) for batch in batches],
        })

    candidate_counts = dashboard.query_pending_candidate_counts()
    plan_start, plan_end = dashboard._railway_plan_day_window()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso_beijing(),
        "source_as_of": {
            "railway_plan_day_start": plan_start,
            "railway_plan_day_end": plan_end,
        },
        "freshness": {"status": "ok", "warnings": []},
        "projects": projects,
        "pending_items": {
            "candidate_counts": candidate_counts,
            "candidate_total": sum(candidate_counts.values()),
            "pending_release_notices": dashboard.query_pending_release_notice_count(),
        },
        "services": dashboard.list_daemons(),
        "jiusan_cycle": jiusan.build_snapshot(),
    }


def encode_snapshot(snapshot: dict[str, Any], *, pretty: bool = False) -> bytes:
    text = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    encoded = (text + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError(f"snapshot exceeds {MAX_OUTPUT_BYTES} byte limit")
    return encoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit freight-dashboard.v1 JSON")
    parser.add_argument("--pretty", action="store_true", help="indent JSON for inspection")
    args = parser.parse_args(argv)
    try:
        sys.stdout.buffer.write(encode_snapshot(build_snapshot(), pretty=args.pretty))
    except Exception as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "error": "snapshot_unavailable",
            "detail": str(exc),
        }
        sys.stdout.buffer.write(json.dumps(error, ensure_ascii=False).encode("utf-8") + b"\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
