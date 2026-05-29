"""Executor runner: bridge live_service monitor → SOP executor chain.

R52: Connects the match/preview layer (live_service) to the execution layer
(departure_text_parser → query_95306 → create_wagon_shipments).

Scope:
- Only jilin_jingang_jinzhou departure_flow.
- Always dry_run=True — never writes to sop_agent.db.
- Writes execution_preview JSON to runtime/execution_previews/.
- Does NOT refresh dispatch_board, send messages, or modify SOP YAML.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_hub.sop.departure_text_parser import DepartureCandidate, parse_departure_text
from ops_hub.sop.monitoring_plan_matcher import MessageEvent
from ops_hub.sop.query_95306_shipments import query_95306_shipments_by_window
from ops_hub.sop.create_wagon_shipments import (
    CreateWagonShipmentsResult,
    create_wagon_shipments_from_candidates,
)
from ops_hub.sop.shipment_query_window import ShipmentQueryResult


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class ExecutionPreview:
    """Complete dry-run preview of a departure executor chain."""

    message_id: str
    group_id: str
    project_id: str
    chain: str = "departure_text → query_95306 → create_wagon_shipments (dry_run)"
    parsed_at: str = ""

    # Step 1: parse_departure_text
    departure_candidate: dict[str, Any] | None = None
    departure_status: str = ""

    # Step 2: query_95306_shipments_by_window
    query_result: dict[str, Any] | None = None
    query_total_candidates: int = 0

    # Step 3: create_wagon_shipments (dry_run)
    wagon_result: dict[str, Any] | None = None
    wagon_status: str = ""
    wagon_planned_insert: int = 0

    # Release batch lookup
    release_batch_id: str = ""
    release_batch_ship: str = ""

    # Overall
    error: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "chain": self.chain,
            "parsed_at": self.parsed_at,
            "steps": {
                "1_parse_departure_text": {
                    "status": self.departure_status,
                    "candidate": self.departure_candidate,
                },
                "2_query_95306": {
                    "total_candidates": self.query_total_candidates,
                    "result": self.query_result,
                },
                "3_create_wagon_shipments": {
                    "status": self.wagon_status,
                    "planned_insert": self.wagon_planned_insert,
                    "result": self.wagon_result,
                },
            },
            "release_batch": {
                "id": self.release_batch_id,
                "ship_name": self.release_batch_ship,
            },
            "error": self.error,
            "skipped_reason": self.skipped_reason,
        }


# ── DB helpers ──────────────────────────────────────────────────────────

def _resolve_sop_db_path() -> Path:
    import os

    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    # Canonical sop-data-hub path
    return Path.home() / "projects" / "repos" / "sop-data-hub" / "data" / "sop_agent.db"


def _find_release_batch(
    ship_name: str,
    destination: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, str] | None:
    """Find a release_batch matching ship_name + destination.

    Prefers 'in_progress' over 'completed' batches.
    Returns {'id': ..., 'ship_name': ...} or None.
    """
    sop_path = _resolve_sop_db_path() if db_path is None else Path(db_path)
    if not sop_path.exists():
        return None

    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        # Try in_progress first
        rows = conn.execute(
            """SELECT id, ship_name, dispatch_status, destination_station, project
               FROM release_batches
               WHERE ship_name LIKE ? AND destination_station LIKE ?
               ORDER BY CASE dispatch_status
                   WHEN 'in_progress' THEN 0
                   WHEN 'active' THEN 1
                   ELSE 2
               END
               LIMIT 3""",
            (f"%{ship_name}%", f"%{destination}%"),
        ).fetchall()

        if rows:
            r = rows[0]
            return {"id": r["id"], "ship_name": r["ship_name"] or ship_name}

        # Try broader match: ship_name only
        rows2 = conn.execute(
            """SELECT id, ship_name, dispatch_status, destination_station, project
               FROM release_batches
               WHERE ship_name LIKE ?
               ORDER BY dispatch_status DESC
               LIMIT 1""",
            (f"%{ship_name}%",),
        ).fetchall()
        if rows2:
            r = rows2[0]
            return {"id": r["id"], "ship_name": r["ship_name"] or ship_name}

        return None
    finally:
        conn.close()


# ── Core runner ─────────────────────────────────────────────────────────

def run_departure_executor_chain(
    event: MessageEvent,
    *,
    runtime_root: str | Path,
    db_path: str | Path | None = None,
) -> ExecutionPreview:
    """Execute the full departure executor chain (dry-run only).

    Chain: parse_departure_text → query_95306 → create_wagon_shipments(dry_run=True)

    Only processes jilin_jingang_jinzhou departures with status="complete".

    Args:
      event: MessageEvent from live_service.
      runtime_root: runtime directory root (for execution_previews/).
      db_path: optional sop_agent.db path.

    Returns:
      ExecutionPreview with all step results.
    """
    rt = Path(runtime_root)
    preview = ExecutionPreview(
        message_id=event.message_id,
        group_id=event.group_id or "",
        project_id="",
        parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    # ── Step 1: parse departure text ──────────────────────────────────
    candidate = parse_departure_text(event)

    preview.departure_candidate = candidate.to_dict()
    preview.departure_status = candidate.status
    preview.project_id = candidate.project_id

    if candidate.status == "no_match":
        preview.skipped_reason = "departure_text_parser: no_match (not a departure message)"

    elif candidate.project_id != "jilin_jingang_jinzhou":
        preview.skipped_reason = (
            f"not jilin_jingang (project_id={candidate.project_id})"
        )

    elif candidate.status == "incomplete":
        preview.skipped_reason = (
            f"departure incomplete: car_count={candidate.car_count}"
        )

    else:
        # ── Step 2: find matching release_batch ───────────────────────
        rb = None
        if candidate.optional_ship_name:
            rb = _find_release_batch(
                candidate.optional_ship_name,
                candidate.destination,
                db_path=db_path,
            )

        if rb is None:
            preview.skipped_reason = (
                f"no release_batch found for ship={candidate.optional_ship_name} "
                f"dest={candidate.destination}"
            )
        else:
            preview.release_batch_id = rb["id"]
            preview.release_batch_ship = rb["ship_name"]

            # ── Step 3: query 95306 ───────────────────────────────────
            origin = "高桥镇"
            dest = candidate.destination or "四平"
            ref_time = candidate.message_time or datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            query_result = query_95306_shipments_by_window(
                origin_station=origin,
                destination_station=dest,
                reference_time=ref_time,
                window_before_minutes=720,
                window_after_minutes=720,
                expected_car_count=candidate.car_count,
                project_id=candidate.project_id,
            )

            query_dict = {
                "origin_station": origin,
                "destination_station": dest,
                "reference_time": ref_time,
                "total_candidates": query_result.total_candidates,
                "exact_match_count": query_result.exact_match_count,
                "ambiguous_count": query_result.ambiguous_count,
                "expected_car_count": candidate.car_count,
                "window": {
                    "start": query_result.window.start_time,
                    "end": query_result.window.end_time,
                },
            }
            preview.query_result = query_dict
            preview.query_total_candidates = query_result.total_candidates

            # ── Step 4: create_wagon_shipments (dry_run) ──────────────
            wagon_result = create_wagon_shipments_from_candidates(
                release_batch_id=preview.release_batch_id,
                departure_candidate=candidate,
                shipment_query_result=query_result,
                dry_run=True,
                db_path=db_path,
            )

            wr_dict = wagon_result.to_dict()
            preview.wagon_result = wr_dict
            preview.wagon_status = wagon_result.status
            preview.wagon_planned_insert = wagon_result.planned_insert_count

    # ── Write execution preview (all code paths) ──────────────────────
    previews_dir = rt / "execution_previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    out_path = previews_dir / f"{event.message_id}.json"
    out_path.write_text(
        json.dumps(preview.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return preview


def run_departure_executor_chain_if_applicable(
    event: MessageEvent,
    *,
    runtime_root: str | Path,
    db_path: str | Path | None = None,
) -> ExecutionPreview | None:
    """Convenience: run chain only if departure text matches jilin_jingang.

    Returns None if the message is not applicable (no departure match or
    not jilin_jingang). Use this as a fire-and-forget hook in live_service.
    """
    # Quick pre-check: must have text content
    if not event.text or not event.text.strip():
        return None

    # Quick pre-check: must contain 四平 keyword
    if "四平" not in event.text:
        return None

    return run_departure_executor_chain(
        event, runtime_root=runtime_root, db_path=db_path
    )
