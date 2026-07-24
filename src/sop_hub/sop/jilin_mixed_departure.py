"""Execute an explicitly segmented mixed-ship Jilin departure event."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sop_hub.sop.departure_text_parser import (
    DepartureCandidate,
    JilinDepartureSegment,
    canonicalize_loading_line,
    parse_departure_text,
    parse_jilin_departure_segments,
)
from sop_hub.sop.shipment_query_window import ShipmentCandidate, ShipmentQueryResult


def _ydid_sort_key(candidate: ShipmentCandidate) -> tuple[int, str, str]:
    digits = re.sub(r"\D", "", candidate.ydid or "")
    return (
        int(digits) if digits else 10**30,
        candidate.ticketed_at or "",
        candidate.wagon_no or "",
    )


def select_exact_ticket_cluster(
    candidates: list[ShipmentCandidate],
    *,
    expected_count: int,
    reference_time: str,
    gap_seconds: int = 180,
) -> list[ShipmentCandidate]:
    """Select one exact-size ticket cluster nearest the source message time."""
    if expected_count <= 0 or not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda item: (item.ticketed_at or "", item.ydid or "", item.wagon_no or ""),
    )
    clusters: list[list[ShipmentCandidate]] = []
    for candidate in ordered:
        if not clusters:
            clusters.append([candidate])
            continue
        try:
            previous = datetime.fromisoformat(clusters[-1][-1].ticketed_at)
            current = datetime.fromisoformat(candidate.ticketed_at)
            separated = (current - previous).total_seconds() > gap_seconds
        except (TypeError, ValueError):
            separated = True
        if separated:
            clusters.append([candidate])
        else:
            clusters[-1].append(candidate)

    exact = [cluster for cluster in clusters if len(cluster) == expected_count]
    if not exact:
        return []
    try:
        reference = datetime.fromisoformat(reference_time[:19].replace("T", " "))
        exact.sort(
            key=lambda cluster: abs(
                (
                    datetime.fromisoformat(cluster[0].ticketed_at)
                    - reference
                ).total_seconds()
            )
        )
    except (TypeError, ValueError):
        pass
    return sorted(exact[0], key=_ydid_sort_key)


def partition_ticket_cluster(
    tickets: list[ShipmentCandidate],
    segments: list[JilinDepartureSegment],
) -> list[tuple[JilinDepartureSegment, list[ShipmentCandidate]]]:
    """Partition a stable ticket sequence by explicit per-ship counts."""
    if sum(segment.car_count for segment in segments) != len(tickets):
        return []
    result: list[tuple[JilinDepartureSegment, list[ShipmentCandidate]]] = []
    offset = 0
    for segment in segments:
        result.append((segment, tickets[offset:offset + segment.car_count]))
        offset += segment.car_count
    return result


def _event_boxes(
    conn: sqlite3.Connection,
    batch_id: str,
    ydids: list[str],
) -> tuple[set[str], set[str]]:
    placeholders = ",".join("?" * len(ydids))
    rows = conn.execute(
        f"SELECT box_no, car_no FROM wagon_container_shipments "
        f"WHERE batch_id=? AND ydid IN ({placeholders})",
        [batch_id, *ydids],
    ).fetchall()
    boxes = {str(row["box_no"] or "") for row in rows if row["box_no"]}
    keys = {
        f"{str(row['box_no'] or '').strip()}|{str(row['car_no'] or '').strip()}"
        for row in rows
        if row["box_no"] and row["car_no"]
    }
    return boxes, keys


def execute_jilin_mixed_departure(
    *,
    input_json: dict[str, Any],
    message_id: str,
    db_path: Path,
    task_id: int,
) -> dict[str, Any]:
    """Run one mixed Jilin event after strict count and cluster validation."""
    from sop_hub.sop.create_wagon_shipments import create_wagon_shipments_from_candidates
    from sop_hub.sop.executor_runner import find_release_batch_with_reason
    from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window

    raw_text = str(input_json.get("text_content") or "")
    received_at = str(input_json.get("received_datetime") or "")
    group_name = str(input_json.get("group_name") or "")
    segments = parse_jilin_departure_segments(raw_text)
    if len(segments) < 2:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": "mixed departure requires at least two explicit ship segments",
        }

    total_expected = sum(segment.car_count for segment in segments)
    base = parse_departure_text(
        raw_text,
        group_id=group_name,
        message_id=message_id,
        message_time=received_at,
    )
    query = query_95306_shipments_by_window(
        origin_station="高桥镇",
        destination_station="四平",
        reference_time=received_at,
        window_before_minutes=720,
        window_after_minutes=720,
        expected_car_count=total_expected,
        project_id="jilin_jingang_jinzhou",
    )
    cluster = select_exact_ticket_cluster(
        query.candidates,
        expected_count=total_expected,
        reference_time=received_at,
    )
    partitions = partition_ticket_cluster(cluster, segments)
    if not partitions:
        return {
            "action": "waiting_95306",
            "status": "pending",
            "output_json": {
                "segments": [segment.to_dict() for segment in segments],
                "expected_total": total_expected,
                "query_total": query.total_candidates,
                "reason": "no exact ticket cluster matching segmented total",
            },
        }

    batch_rows: list[dict[str, Any]] = []
    for segment, tickets in partitions:
        batch, reason, candidates = find_release_batch_with_reason(
            segment.ship_name, "四平", db_path=db_path,
        )
        if not batch:
            return {
                "action": "failed",
                "status": "failed",
                "error_message": (
                    f"no open batch for {segment.ship_name}: {reason}; "
                    f"candidates={candidates}"
                ),
            }
        batch_rows.append(
            {
                "segment": segment,
                "tickets": tickets,
                "batch": batch,
            }
        )

    ingest_results: list[dict[str, Any]] = []
    all_ydids: list[str] = []
    loading_line = canonicalize_loading_line(base.lane_or_track)
    for item in batch_rows:
        segment = item["segment"]
        tickets = item["tickets"]
        batch = item["batch"]
        segment_candidate = replace(
            base,
            car_count=segment.car_count,
            optional_ship_name=segment.ship_name,
            raw_text=f"{loading_line} 四平铁 {segment.ship_name}{segment.car_count}节",
        )
        subset = ShipmentQueryResult(
            window=query.window,
            origin_station=query.origin_station,
            destination_station=query.destination_station,
            expected_car_count=segment.car_count,
            total_candidates=len(tickets),
            exact_match_count=len(tickets),
            ambiguous_count=0,
            candidates=tickets,
        )
        result = create_wagon_shipments_from_candidates(
            release_batch_id=batch["id"],
            departure_candidate=segment_candidate,
            shipment_query_result=subset,
            db_path=db_path,
        )
        ingest_results.append(
            {
                "ship_name": segment.ship_name,
                "batch_id": batch["id"],
                "car_count": segment.car_count,
                "result": result.to_dict(),
            }
        )
        if result.status != "safe_to_apply":
            return {
                "action": "failed",
                "status": "failed",
                "error_message": f"{segment.ship_name} ingest blocked: {result.warnings}",
                "output_json": {"ingest": ingest_results},
            }
        all_ydids.extend(ticket.ydid for ticket in tickets)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(all_ydids))
        conn.execute(
            f"UPDATE wagon_container_shipments SET loading_line=?, "
            f"source_message_id=?, source_group_id=?, updated_at=datetime('now') "
            f"WHERE ydid IN ({placeholders})",
            [loading_line, message_id, group_name, *all_ydids],
        )
        conn.commit()
    finally:
        conn.close()

    for item in batch_rows:
        batch_id = item["batch"]["id"]
        try:
            from sop_hub.sop.shipped_weight import compute_for_release_batch
            from sop_hub.sop.lifecycle_transition import advance_lifecycle

            compute_for_release_batch(batch_id, db_path=db_path)
            advance_lifecycle(
                batch_id,
                "loading",
                reason=f"mixed departure {message_id}",
                triggered_by="jilin_mixed_departure",
                db_path=db_path,
            )
        except Exception:
            pass

    from sop_hub.sop.departure_excel import generate_dispatch_event_excel
    excel = generate_dispatch_event_excel(container_ydids=all_ydids, db_path=db_path)
    if not excel.output_path or excel.error:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": excel.error or "mixed departure excel generation failed",
            "output_json": {"ingest": ingest_results},
        }

    event_hash = hashlib.sha1("|".join(sorted(all_ydids)).encode()).hexdigest()[:16]
    business_key = f"mixed:{event_hash}:{len(all_ydids)}"
    from sop_hub.sop.external_action_log import (
        build_idempotency_key,
        get_action_by_key,
        mark_external_action_executed,
        mark_external_action_failed,
        plan_external_action,
    )

    action_keys: dict[str, str] = {}
    for action_type, target in (
        ("generate_shipping_excel", "filesystem"),
        ("factory_upload_submit", "jilin_factory"),
        ("send_shipping_excel_wechat", "wechat"),
    ):
        key = build_idempotency_key(
            "jilin_jingang_jinzhou", action_type, business_key,
        )
        action_keys[action_type] = key
        plan_external_action(
            db_path=db_path,
            workflow_task_id=task_id,
            message_inbox_id=int(input_json.get("message_inbox_id") or 0),
            message_id=message_id,
            project_id="jilin_jingang_jinzhou",
            action_type=action_type,
            idempotency_key=key,
            target_system=target,
            artifact_path=excel.output_path,
            request_json={
                "segments": [segment.to_dict() for segment in segments],
                "ydids": all_ydids,
            },
        )
    mark_external_action_executed(
        action_keys["generate_shipping_excel"],
        db_path=db_path,
        response_json={"path": excel.output_path, "rows": excel.row_count},
        artifact_path=excel.output_path,
    )

    upload_summary: dict[str, Any] = {}
    upload_ok = False
    upload_key = action_keys["factory_upload_submit"]
    existing_upload = get_action_by_key(upload_key, db_path=db_path)
    if existing_upload and existing_upload.get("action_status") == "executed":
        upload_ok = True
        upload_summary = {"skipped": True, "reason": "already executed"}
    else:
        from sop_hub.sop.factory_upload import upload_dispatch_event_wagons
        from sop_hub.sop.factory_verify import verify_factory_upload

        placeholders = ",".join("?" * len(all_ydids))
        conn = sqlite3.connect(str(db_path))
        try:
            portal_row = conn.execute(
                f"SELECT COUNT(*), SUM(portal_id IS NOT NULL) "
                f"FROM wagon_container_shipments WHERE ydid IN ({placeholders})",
                all_ydids,
            ).fetchone()
        finally:
            conn.close()
        event_box_count = int((portal_row or (0, 0))[0] or 0)
        portal_id_count = int((portal_row or (0, 0))[1] or 0)

        # 上传后进程可能在反查阶段异常退出。portal_id 是 POST 后从门户回查并
        # 回填的持久证据；若本次 90 箱已全有 portal_id，重跑只做 verify，
        # 禁止再次 POST。
        recovered_after_upload = (
            event_box_count > 0 and portal_id_count == event_box_count
        )
        upload = None
        if recovered_after_upload:
            upload_ok = True
        else:
            upload = upload_dispatch_event_wagons(
                project_id="jilin_jingang_jinzhou",
                container_ydids=all_ydids,
                db_path=db_path,
            )
            upload_ok = (
                upload.login_success
                and upload.total_wagons > 0
                and upload.failure_count == 0
                and upload.success_count == upload.total_wagons
            )
        verify_rows: list[dict[str, Any]] = []
        if upload_ok:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                for item in batch_rows:
                    bid = item["batch"]["id"]
                    ydids = [ticket.ydid for ticket in item["tickets"]]
                    boxes, unique_keys = _event_boxes(conn, bid, ydids)
                    batch_row = conn.execute(
                        "SELECT order_identifier FROM release_batches WHERE id=?",
                        (bid,),
                    ).fetchone()
                    order_id = str(
                        (batch_row["order_identifier"] if batch_row else "") or ""
                    )
                    verify = verify_factory_upload(
                        order_id,
                        expected_box_numbers=boxes,
                        expected_unique_keys=unique_keys,
                        expected_count=len(unique_keys),
                        db_path=db_path,
                    )
                    row = verify.to_dict()
                    verify_rows.append(row)
                    upload_ok = (
                        upload_ok
                        and bool(row.get("login_ok"))
                        and not row.get("error")
                        and int(row.get("missing_box_count") or 0) == 0
                        and int(row.get("duplicate_box_count") or 0) == 0
                    )
            finally:
                conn.close()
        upload_summary = {
            "recovered_after_upload": recovered_after_upload,
            "portal_id_count": portal_id_count,
            "login_success": True if recovered_after_upload else upload.login_success,
            "payloads": event_box_count if recovered_after_upload else upload.total_wagons,
            "success": event_box_count if recovered_after_upload else upload.success_count,
            "failure": 0 if recovered_after_upload else upload.failure_count,
            "verify": verify_rows,
        }
        if upload_ok:
            mark_external_action_executed(
                upload_key, db_path=db_path, response_json=upload_summary,
                artifact_path=excel.output_path,
            )
        else:
            mark_external_action_failed(
                upload_key,
                json.dumps(upload_summary, ensure_ascii=False),
                db_path=db_path,
            )

    if not upload_ok:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": "factory upload or verification incomplete",
            "output_json": {
                "segments": [segment.to_dict() for segment in segments],
                "ingest": ingest_results,
                "excel": excel.__dict__,
                "factory_upload": upload_summary,
            },
        }

    send_key = action_keys["send_shipping_excel_wechat"]
    existing_send = get_action_by_key(send_key, db_path=db_path)
    send_ok = bool(existing_send and existing_send.get("action_status") == "executed")
    send_summary: dict[str, Any] = {"skipped": send_ok}
    if not send_ok:
        from sop_hub.sop.send_excel import send_to_wechat

        sent = send_to_wechat(
            target="[GROUP013]",
            message=(
                f"吉林金钢发运数据 共{total_expected}车"
                f"（{' + '.join(f'{s.ship_name}{s.car_count}车' for s in segments)}）"
            ),
            file_path=excel.output_path,
        )
        send_ok = sent.success
        send_summary = sent.__dict__
        if send_ok:
            mark_external_action_executed(
                send_key, db_path=db_path, response_json=send_summary,
                artifact_path=excel.output_path,
            )
        else:
            mark_external_action_failed(
                send_key, sent.error or sent.output[-500:], db_path=db_path,
            )

    return {
        "action": "applied" if upload_ok and send_ok else "failed",
        "status": "succeeded" if upload_ok and send_ok else "failed",
        "error_message": "" if upload_ok and send_ok else "external action incomplete",
        "output_json": {
            "segments": [segment.to_dict() for segment in segments],
            "ticket_cluster": {
                "count": len(cluster),
                "first_ydid": cluster[0].ydid,
                "last_ydid": cluster[-1].ydid,
            },
            "ingest": ingest_results,
            "excel": excel.__dict__,
            "factory_upload": upload_summary,
            "wechat_send": send_summary,
        },
    }
