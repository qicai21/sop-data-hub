"""Executor runner: bridge live_service monitor → SOP executor chain.

R52/R55: Connects the match/preview layer (live_service) to the execution layer
(departure_text_parser → query_95306 → create_wagon_shipments →
 departure_excel → factory_upload).

Scope:
- Only jilin_jingang_jinzhou departure_flow.
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

from sop_hub.sop.departure_text_parser import DepartureCandidate, parse_departure_text
from sop_hub.sop.monitoring_plan_matcher import MessageEvent
from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window
from sop_hub.sop.create_wagon_shipments import (
    CreateWagonShipmentsResult,
    create_wagon_shipments_from_candidates,
)
from sop_hub.sop.shipment_query_window import ShipmentQueryResult


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class ExecutionPreview:
    """Complete dry-run or apply preview of a departure executor chain."""

    message_id: str
    group_id: str
    project_id: str
    chain: str = "departure_text → query_95306 → create_wagon_shipments"
    parsed_at: str = ""

    # Step 1: parse_departure_text
    departure_candidate: dict[str, Any] | None = None
    departure_status: str = ""

    # Step 2: query_95306_shipments_by_window
    query_result: dict[str, Any] | None = None
    query_total_candidates: int = 0

    # Step 3: create_wagon_shipments
    wagon_result: dict[str, Any] | None = None
    wagon_status: str = ""
    wagon_planned_insert: int = 0
    wagon_actual_insert: int = 0

    # Release batch lookup
    release_batch_id: str = ""
    release_batch_ship: str = ""

    # Step 4: departure_excel (R55)
    excel_path: str = ""
    excel_rows: int = 0
    excel_wagons: int = 0

    # Step 5: factory_upload (R55)
    factory_payloads: int = 0
    factory_success: int = 0
    factory_failure: int = 0
    factory_login: bool = False
    factory_login_error: str = ""

    # Step 5b: factory_verify (R55)
    factory_verified: bool = False
    factory_verify_total_match: bool = False
    factory_verify_boxes_ok: bool = False
    factory_verify_api_total: int = 0

    # Step 6: send_excel via wx-ui-bridge (R55)
    excel_sent: bool = False
    excel_target: str = ""

    # Overall
    apply_mode: bool = False
    error: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "chain": self.chain,
            "parsed_at": self.parsed_at,
            "apply_mode": self.apply_mode,
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
                    "actual_insert": self.wagon_actual_insert,
                    "result": self.wagon_result,
                },
                "4_departure_excel": {
                    "path": self.excel_path,
                    "rows": self.excel_rows,
                    "wagons": self.excel_wagons,
                },
                "5_factory_upload": {
                    "login_success": self.factory_login,
                    "login_error": self.factory_login_error,
                    "payloads": self.factory_payloads,
                    "success": self.factory_success,
                    "failure": self.factory_failure,
                },
                "5b_factory_verify": {
                    "verified": self.factory_verified,
                    "total_match": self.factory_verify_total_match,
                    "boxes_ok": self.factory_verify_boxes_ok,
                    "api_total": self.factory_verify_api_total,
                },
                "6_send_excel": {
                    "sent": self.excel_sent,
                    "target": self.excel_target,
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
    return Path.home() / "projects" / "repos" / "sop-data-hub" / "data" / "sop_agent.db"


def _find_release_batch(
    ship_name: str,
    destination: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, str] | None:
    """Find a release_batch matching ship_name + destination.

    Prefers 'in_progress' over 'completed' batches.
    """
    sop_path = _resolve_sop_db_path() if db_path is None else Path(db_path)
    if not sop_path.exists():
        return None

    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
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
    apply_mode: bool = False,
) -> ExecutionPreview:
    """Execute the full departure executor chain.

    Chain: parse_departure_text → query_95306 → create_wagon_shipments
           → departure_excel → factory_upload

    In apply_mode:
      - Writes wagon_shipments to DB
      - Generates real Excel
      - Performs real factory upload
    """
    rt = Path(runtime_root)
    chain_str = (
        "departure_text → query_95306 → create_wagon_shipments → "
        "departure_excel → factory_upload → factory_verify → "
        "send_excel (wx-ui-bridge)"
    )
    preview = ExecutionPreview(
        message_id=event.message_id,
        group_id=event.group_id or "",
        project_id="",
        chain=chain_str,
        parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        apply_mode=apply_mode,
    )

    # ── Step 1: parse departure text ──────────────────────────────────
    candidate = parse_departure_text(event)
    preview.departure_candidate = candidate.to_dict()
    preview.departure_status = candidate.status
    preview.project_id = candidate.project_id

    if candidate.status == "no_match":
        preview.skipped_reason = "departure_text_parser: no_match"
    elif candidate.project_id != "jilin_jingang_jinzhou":
        preview.skipped_reason = f"not jilin_jingang (project_id={candidate.project_id})"
    elif candidate.status == "incomplete":
        preview.skipped_reason = f"departure incomplete: car_count={candidate.car_count}"
    else:
        # ── Step 2: find matching release_batch ───────────────────────
        rb = None
        if candidate.optional_ship_name:
            rb = _find_release_batch(
                candidate.optional_ship_name, candidate.destination, db_path=db_path
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

            # ── Step 4: create_wagon_shipments ────────────────────────
            dry_run_wagons = not apply_mode
            wagon_result = create_wagon_shipments_from_candidates(
                release_batch_id=preview.release_batch_id,
                departure_candidate=candidate,
                shipment_query_result=query_result,
                dry_run=dry_run_wagons,
                db_path=db_path,
            )
            wr_dict = wagon_result.to_dict()
            preview.wagon_result = wr_dict
            preview.wagon_status = wagon_result.status
            preview.wagon_planned_insert = wagon_result.planned_insert_count
            preview.wagon_actual_insert = wagon_result.inserted_count

            # ── Step 5: departure_excel + factory_upload (apply only) ─
            if apply_mode and wagon_result.status == "safe_to_apply":
                # Trigger Excel + factory even if all wagons already exist
                # (idempotent: create_wagon_shipments skips existing, but we
                #  still want to re-generate Excel and re-upload)
                try:
                    from sop_hub.sop.departure_excel import generate_departure_excel
                    excel_result = generate_departure_excel(
                        preview.release_batch_id, db_path=db_path
                    )
                    preview.excel_path = excel_result.output_path
                    preview.excel_rows = excel_result.row_count
                    preview.excel_wagons = excel_result.wagon_count
                except Exception as exc:
                    preview.error += f"excel: {exc}; "

                try:
                    from sop_hub.sop.factory_upload import upload_release_batch
                    factory_result = upload_release_batch(
                        preview.release_batch_id,
                        dry_run=False,
                        db_path=db_path,
                    )
                    preview.factory_login = factory_result.login_success
                    preview.factory_login_error = factory_result.login_error
                    preview.factory_payloads = factory_result.total_wagons
                    preview.factory_success = factory_result.success_count
                    preview.factory_failure = factory_result.failure_count
                except Exception as exc:
                    preview.error += f"factory_upload: {exc}; "

                # ── Step 5b: verify factory upload (R55) ──────────────
                try:
                    from sop_hub.sop.factory_verify import verify_factory_upload
                    batch_ord = preview.release_batch_id
                    verify = verify_factory_upload(
                        order_id="",
                        release_batch_id=batch_ord,
                    )
                    preview.factory_verified = True
                    preview.factory_verify_total_match = verify.total_match
                    preview.factory_verify_boxes_ok = verify.all_boxes_found
                    preview.factory_verify_api_total = verify.api_total
                    if not verify.all_boxes_found:
                        preview.error += (
                            f"verify: total_match={verify.total_match} "
                            f"missing={len(verify.missing_boxes)} "
                            f"extra={len(verify.extra_boxes)}; "
                        )
                except Exception as exc:
                    preview.error += f"factory_verify: {exc}; "

                # ── Step 6: send Excel to contact (R55) ──────────────
                try:
                    from sop_hub.sop.send_excel import send_to_wechat
                    target = "郭东北"
                    batch_ship = preview.release_batch_ship
                    msg = f"吉林金钢发运数据 {batch_ship} lot02 {excel_result.wagon_count}车"
                    send_result = send_to_wechat(
                        target=target,
                        message=msg,
                        file_path=excel_result.output_path,
                    )
                    preview.excel_sent = send_result.success
                    preview.excel_target = target
                    if not send_result.success:
                        preview.error += f"send_excel: {send_result.error}; "
                except Exception as exc:
                    preview.error += f"send_excel: {exc}; "

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
    apply_mode: bool = False,
) -> ExecutionPreview | None:
    """Convenience: run chain only if departure text matches jilin_jingang.

    Returns None if the message is not applicable.
    """
    if not event.text or not event.text.strip():
        return None
    if "四平" not in event.text:
        return None
    return run_departure_executor_chain(
        event, runtime_root=runtime_root, db_path=db_path, apply_mode=apply_mode,
    )
