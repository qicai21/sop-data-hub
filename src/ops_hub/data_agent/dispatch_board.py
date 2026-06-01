from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
import json
from functools import lru_cache
from pathlib import Path
import sqlite3
import time
from typing import Any
from urllib.parse import quote

from ops_hub.data_agent.agent import active_business_sop_project_tokens, parse_business_text_fields


STATUS_LABELS = {
    "in_progress": "发运中",
    "active": "发运中",
    "delivered": "已发完",
    "completed": "已发完",
    "suspended": "暂停",
    "cancelled": "不发运",
    "pending": "待人工确认",
    "ambiguous": "待人工确认",
}

MANUAL_CANDIDATE_STATUSES = {"pending", "ambiguous"}
MATCHED_CANDIDATE_STATUSES = {"candidate", "committed"}


def render_dispatch_board(
    *,
    business_db_path: str | Path,
    output_path: str | Path,
    rail_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render a static dispatch board from the business DB and optional read-only 95306 linkage DB."""
    business_db_path = Path(business_db_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(business_db_path) as business_db:
        business_db.row_factory = sqlite3.Row
        release_rows = _fetch_release_batches(business_db)
        candidate_summary = _fetch_candidate_summary(business_db)
        audit_paths = _fetch_audit_paths(business_db)
        text_pending_rows = _fetch_text_pending_audits(business_db)
        unresolved_work_items = _fetch_unresolved_work_items(business_db)

    formal_summary: dict[str, dict[str, Any]] = {}
    if rail_db_path:
        formal_summary = _fetch_formal_summary_read_only(Path(rail_db_path))

    html = _build_html(
        business_db_path=business_db_path,
        rail_db_path=Path(rail_db_path) if rail_db_path else None,
        release_rows=release_rows,
        candidate_summary=candidate_summary,
        formal_summary=formal_summary,
        audit_paths=audit_paths,
        text_pending_rows=text_pending_rows,
        unresolved_work_items=unresolved_work_items,
    )
    output_path.write_text(html, encoding="utf-8")
    visible_release_ids = {str(row.get("id") or "") for row in release_rows}
    return {
        "output_path": str(output_path),
        "release_batch_count": len(release_rows),
        "active_release_batch_count": sum(1 for row in release_rows if row.get("dispatch_status") == "in_progress"),
        "candidate_count": sum(item.get("matched_candidate_count", 0) for item in candidate_summary.values()),
        "manual_pending_candidate_count": _manual_pending_count(candidate_summary),
        "unresolved_total_count": _unresolved_total_count(unresolved_work_items),
        "pending_text_release_count": len(unresolved_work_items.get("text_release", [])),
        "pending_departure_plan_count": len(unresolved_work_items.get("departure_plan", [])),
        "pending_inspection_assignment_count": len(unresolved_work_items.get("inspection_assignment", [])),
        "pending_inspection_validation_count": len(unresolved_work_items.get("inspection_validation", [])),
        "formal_match_count": sum(item.get("formal_match_count", 0) for key, item in formal_summary.items() if key in visible_release_ids),
        "formal_weight": sum(float(item.get("formal_weight") or 0) for key, item in formal_summary.items() if key in visible_release_ids),
    }


def _fetch_release_batches(db: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(db, "release_batches"):
        return []
    rows = db.execute(
        """
        SELECT id, project, ship_name, destination_station, batch_sequence,
               batch_quantity, total_planned_quantity, batch_date,
               cargo_name, cargo_product_name,
               dispatch_status, dispatch_status_note, source_file_name,
               source_json, updated_at, plan_id, order_id, order_identifier,
               contract_no, actual_wagon_count, confirmed_received_at,
               shipped_weight_tons, remaining_weight_tons, unresolved_wagon_count,
               shipped_weight_last_computed_at
        FROM release_batches
        ORDER BY
          project ASC,
          CASE dispatch_status
            WHEN 'in_progress' THEN 1
            WHEN 'suspended' THEN 2
            WHEN 'completed' THEN 3
            WHEN 'cancelled' THEN 4
            ELSE 9
          END,
          COALESCE(batch_date, notice_date, updated_at) DESC,
          ship_name ASC,
          batch_sequence ASC
        """
    ).fetchall()
    tokens = active_business_sop_project_tokens()
    return [dict(row) for row in rows if str(row["project"] or "").strip() in tokens]


def _fetch_candidate_summary(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = defaultdict(_empty_candidate_summary)
    if not _table_exists(db, "inspection_ingestion_candidates"):
        return {}
    rows = db.execute(
        """
        SELECT release_batch_id, status, wagon_count, payload_json
        FROM inspection_ingestion_candidates
        """
    ).fetchall()
    for row in rows:
        status = row["status"] or ""
        wagon_count = int(row["wagon_count"] or 0)
        release_batch_id = str(row["release_batch_id"] or "")
        candidate_ids = [str(item) for item in (_json_object(row["payload_json"]).get("_candidate_release_batch_ids") or []) if str(item).strip()]
        keys = [release_batch_id] if release_batch_id else []
        if not keys:
            keys = ["__unassigned__"]
        for key in keys:
            item = summary[key]
            item["by_status"][status] = item["by_status"].get(status, 0) + 1
            item["wagon_count"] += wagon_count
            if status in MATCHED_CANDIDATE_STATUSES:
                item["matched_candidate_count"] += 1
            if status in MANUAL_CANDIDATE_STATUSES:
                item["manual_pending_candidate_count"] += 1
            for candidate_id in candidate_ids:
                if candidate_id not in item["candidate_release_batch_ids"]:
                    item["candidate_release_batch_ids"].append(candidate_id)
        if status in MANUAL_CANDIDATE_STATUSES and candidate_ids:
            for candidate_id in candidate_ids:
                item = summary[candidate_id]
                item["manual_pending_candidate_count"] += 1
                item["by_status"][status] = item["by_status"].get(status, 0) + 1
                for nested_id in candidate_ids:
                    if nested_id not in item["candidate_release_batch_ids"]:
                        item["candidate_release_batch_ids"].append(nested_id)
    _merge_text_release_audit_summary(db, summary)
    return dict(summary)


def _merge_text_release_audit_summary(db: sqlite3.Connection, summary: dict[str, dict[str, Any]]) -> None:
    if not _table_exists(db, "image_ingestion_audit"):
        return
    rows = db.execute(
        """
        SELECT db_record_ids, status, reason, requires_manual_review
        FROM image_ingestion_audit
        WHERE message_type = 'text'
          AND classified_category = '文字放货指令'
          AND requires_manual_review = 1
        """
    ).fetchall()
    for row in rows:
        candidate_ids = [str(item) for item in _json_array(row["db_record_ids"]) if str(item).strip()]
        if not candidate_ids:
            key = "__unassigned__"
            item = summary[key]
            item["manual_pending_candidate_count"] += 1
            status = str(row["status"] or "pending")
            item["by_status"][status] = item["by_status"].get(status, 0) + 1
            continue
        status = str(row["status"] or "pending")
        for candidate_id in candidate_ids:
            item = summary[candidate_id]
            item["manual_pending_candidate_count"] += 1
            item["by_status"][status] = item["by_status"].get(status, 0) + 1
            for nested_id in candidate_ids:
                if nested_id not in item["candidate_release_batch_ids"]:
                    item["candidate_release_batch_ids"].append(nested_id)


def _fetch_text_pending_audits(db: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(db, "image_ingestion_audit"):
        return []
    rows = db.execute(
        """
        SELECT raw_image_path, db_record_ids, status, reason, created_at
        FROM image_ingestion_audit
        WHERE message_type = 'text'
          AND classified_category = '文字放货指令'
          AND requires_manual_review = 1
        ORDER BY created_at DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        fields = parse_business_text_fields(str(row["raw_image_path"] or ""))
        result.append(
            {
                "fields": fields,
                "candidate_ids": [str(item) for item in _json_array(row["db_record_ids"]) if str(item).strip()],
                "status": row["status"] or "pending",
                "reason": row["reason"] or "",
                "created_at": row["created_at"] or "",
            }
        )
    return result


def _fetch_unresolved_work_items(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Fetch operator-facing unresolved work items for the top of the dispatch board."""
    items: dict[str, list[dict[str, Any]]] = {
        "text_release": [],
        "departure_plan": [],
        "inspection_assignment": [],
        "inspection_validation": [],
    }
    if _table_exists(db, "image_ingestion_audit"):
        text_rows = db.execute(
            """
            SELECT id, raw_image_path, db_record_ids, status, reason, created_at
            FROM image_ingestion_audit
            WHERE message_type = 'text'
              AND classified_category = '文字放货指令'
              AND requires_manual_review = 1
            ORDER BY created_at DESC
            """
        ).fetchall()
        for row in text_rows:
            fields = parse_business_text_fields(str(row["raw_image_path"] or ""))
            items["text_release"].append(
                {
                    "kind": "待匹配文字放货消息",
                    "audit_id": str(row["id"] or ""),
                    "ship_name": fields.get("船名") or "",
                    "cargo_name": fields.get("货名") or "",
                    "quantity": fields.get("数量") or "",
                    "plan_id": fields.get("计划号") or "",
                    "contract_no": fields.get("合同号") or "",
                    "status": row["status"] or "pending",
                    "reason": row["reason"] or "",
                    "candidate_ids": [str(item) for item in _json_array(row["db_record_ids"]) if str(item).strip()],
                    "source_file": "文字消息",
                    "json_path": "",
                    "created_at": row["created_at"] or "",
                }
            )

        plan_rows = db.execute(
            """
            SELECT id, raw_image_path, classified_image_path, extraction_json_path, status, reason, db_action, created_at
            FROM image_ingestion_audit
            WHERE message_type = 'image'
              AND classified_category = '出港计划通知单'
              AND NOT (status = 'ingested' AND COALESCE(db_action, '') LIKE 'release_batch%')
              AND COALESCE(db_action, '') NOT IN ('discarded_by_operator')
              AND (
                requires_manual_review = 1
                OR status IN ('pending', 'extracted', 'failed')
                OR COALESCE(db_action, '') IN ('', 'none')
                OR COALESCE(reason, '') LIKE '%non_sop%'
              )
            ORDER BY created_at DESC
            """
        ).fetchall()
        for row in plan_rows:
            summary = _document_summary_from_json(row["extraction_json_path"])
            items["departure_plan"].append(
                {
                    "kind": "待匹配出港计划/放货图片",
                    "audit_id": str(row["id"] or ""),
                    "ship_name": summary.get("ship_name") or summary.get("船名") or "",
                    "cargo_name": summary.get("cargo_name") or summary.get("cargo") or summary.get("货名") or "",
                    "quantity": summary.get("batch_quantity") or summary.get("quantity") or summary.get("计划量") or "",
                    "plan_id": summary.get("plan_id") or summary.get("计划号") or "",
                    "contract_no": summary.get("contract_no") or summary.get("合同号") or "",
                    "status": row["status"] or "",
                    "reason": row["reason"] or "",
                    "candidate_ids": [],
                    "source_file": _resolved_source_image_path(row["raw_image_path"], row["classified_image_path"]),
                    "json_path": row["extraction_json_path"] or "",
                    "created_at": row["created_at"] or "",
                }
            )

        validation_rows = db.execute(
            """
            SELECT id, raw_image_path, classified_image_path, extraction_json_path, db_record_ids, status, reason, created_at
            FROM image_ingestion_audit
            WHERE message_type = 'image'
              AND classified_category = '检装车通知单'
              AND db_action = 'candidate_pending'
              AND reason = 'matched_release_batch_waiting_95306_validation'
            ORDER BY created_at DESC
            """
        ).fetchall()
        for row in validation_rows:
            summary = _document_summary_from_json(row["extraction_json_path"])
            items["inspection_validation"].append(
                {
                    "kind": "待95306校验装车候选",
                    "audit_id": str(row["id"] or ""),
                    "ship_name": summary.get("ship_name") or summary.get("ship") or summary.get("船名") or "",
                    "cargo_name": summary.get("cargo_name") or summary.get("cargo") or summary.get("货名") or "",
                    "quantity": summary.get("wagon_count") or summary.get("actual_wagon_count") or summary.get("节数") or "",
                    "plan_id": "",
                    "contract_no": "",
                    "status": row["status"] or "pending",
                    "reason": row["reason"] or "",
                    "candidate_ids": [str(item) for item in _json_array(row["db_record_ids"]) if str(item).strip()],
                    "source_file": _resolved_source_image_path(row["raw_image_path"], row["classified_image_path"]),
                    "json_path": row["extraction_json_path"] or "",
                    "created_at": row["created_at"] or "",
                }
            )

    if _table_exists(db, "inspection_ingestion_candidates"):
        candidate_rows = db.execute(
            """
            SELECT source_file_name, status, reason, release_batch_id, wagon_count, payload_json, created_at
            FROM inspection_ingestion_candidates
            WHERE status IN ('pending', 'ambiguous')
              AND (
                COALESCE(release_batch_id, '') = ''
                OR reason = 'no_release_batch_candidate'
                OR reason LIKE '%ambiguous%'
              )
            ORDER BY created_at DESC
            """
        ).fetchall()
        for row in candidate_rows:
            payload = _json_object(row["payload_json"])
            items["inspection_assignment"].append(
                {
                    "kind": "待指认检装车通知单",
                    "ship_name": payload.get("ship_name") or payload.get("ship") or payload.get("船名") or "",
                    "cargo_name": payload.get("cargo_name") or payload.get("cargo") or payload.get("货名") or "",
                    "quantity": row["wagon_count"] or payload.get("wagon_count") or payload.get("rows_count") or "",
                    "plan_id": "",
                    "contract_no": "",
                    "status": row["status"] or "pending",
                    "reason": row["reason"] or "",
                    "candidate_ids": [str(item) for item in (payload.get("_candidate_release_batch_ids") or []) if str(item).strip()],
                    "source_file": row["source_file_name"] or "",
                    "json_path": "",
                    "created_at": row["created_at"] or "",
                }
            )
    return items


def _unresolved_total_count(items: dict[str, list[dict[str, Any]]]) -> int:
    return sum(len(rows) for rows in items.values())


def _document_summary_from_json(path_text: Any) -> dict[str, Any]:
    path = Path(str(path_text or ""))
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    summary: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float)) or value is None:
            summary[key] = value
    for nested_key in ("extracted", "structured", "data", "fields", "payload"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            for key, value in nested.items():
                if key not in summary and (isinstance(value, (str, int, float)) or value is None):
                    summary[key] = value
    return summary


def _empty_candidate_summary() -> dict[str, Any]:
    return {
        "matched_candidate_count": 0,
        "manual_pending_candidate_count": 0,
        "wagon_count": 0,
        "by_status": {},
        "candidate_release_batch_ids": [],
    }


def _manual_pending_count(summary: dict[str, dict[str, Any]]) -> int:
    return sum(int(item.get("manual_pending_candidate_count") or 0) for item in summary.values())


def _fetch_audit_paths(db: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not _table_exists(db, "image_ingestion_audit"):
        return {}
    rows = db.execute(
        """
        SELECT raw_image_path, classified_image_path, extraction_json_path, project_archive_paths
        FROM image_ingestion_audit
        ORDER BY created_at DESC
        """
    ).fetchall()
    paths: dict[str, dict[str, str]] = {}
    for row in rows:
        source_candidates = [row["classified_image_path"], row["raw_image_path"]]
        keys = {Path(str(item)).name for item in source_candidates if item}
        keys.update({Path(str(item)).stem for item in source_candidates if item})
        archive_paths = _json_object(row["project_archive_paths"])
        status_path = str(archive_paths.get("status_path") or archive_paths.get("status") or "")
        if not status_path:
            status_path = _derive_status_path(row["raw_image_path"], row["classified_image_path"])
        path_info = {
            "raw_image_path": str(row["raw_image_path"] or ""),
            "classified_image_path": str(row["classified_image_path"] or ""),
            "json_path": str(row["extraction_json_path"] or ""),
            "status_path": status_path,
        }
        for key in keys:
            paths.setdefault(key, path_info)
    return paths


def _derive_status_path(raw_image_path: Any, classified_image_path: Any = "") -> str:
    """Infer the per-image _status sidecar path when the audit row predates explicit storage.

    The live runner writes ``<group>/_status/<stem>.json`` next to the group archive,
    but older audit rows only recorded image and extraction paths.  The board should
    still link to the sidecar when it can be derived from those paths.
    """
    candidates = [str(classified_image_path or "").strip(), str(raw_image_path or "").strip()]
    for candidate_text in candidates:
        if not candidate_text:
            continue
        candidate = Path(candidate_text)
        if not candidate.suffix:
            continue
        parents: list[Path] = []
        if candidate.parent.name in {"出港计划通知单", "检装车通知单", "出港放货", "放货记录"}:
            parents.append(candidate.parent.parent)
        if candidate.parent.name.startswith("20"):
            parents.append(candidate.parent.parent)
        parents.append(candidate.parent)
        for parent in parents:
            status_path = parent / "_status" / f"{candidate.stem}.json"
            if status_path.exists():
                return str(status_path)
    return ""


def _augment_formal_summary_from_sop_db(
    business_db_path: Path, formal_summary: dict[str, dict[str, Any]]
) -> None:
    """Read wagon_shipments from sop_agent.db and merge counts into formal_summary.

    Populates formal_match_count and car_details for batches that have wagons in
    wagon_shipments but not in the 95306 DB (e.g., wagons created via
    departure_text → create_wagon_shipments pipeline).
    """
    if not business_db_path.exists():
        return
    with sqlite3.connect(business_db_path) as db:
        db.row_factory = sqlite3.Row
        if not _table_exists(db, "wagon_shipments"):
            return
        rows = db.execute(
            """
            SELECT ws.batch_id, ws.car_no, ws.departed_at, ws.delivered_at,
                   COALESCE(ws.confirmed_received_at, '') as confirmed_received_at
            FROM wagon_shipments ws
            ORDER BY ws.batch_id, ws.car_no
            """
        ).fetchall()
    for row in rows:
        batch_id = str(row["batch_id"] or "")
        if not batch_id:
            continue
        item = formal_summary.setdefault(
            batch_id,
            {"formal_match_count": 0, "formal_weight": 0.0, "car_details": []},
        )
        # Only count if not already in car_details (dedup by car_no)
        existing_cars = {d.get("car_no", "") for d in item["car_details"]}
        car_no = str(row["car_no"] or "")
        if car_no and car_no not in existing_cars:
            item["car_details"].append({
                "car_no": car_no,
                "time": str(row["departed_at"] or row["delivered_at"] or ""),
                "inspection_file": "",
            })
            item["formal_match_count"] = len(item["car_details"])


def _fetch_formal_summary_read_only(rail_db_path: Path) -> dict[str, dict[str, Any]]:
    if not rail_db_path.exists():
        return {}
    uri = f"file:{rail_db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        if not _table_exists(db, "shipment_release_batch_matches"):
            return {}
        rows = db.execute(
            """
            SELECT release_batch_id,
                   COUNT(*) AS formal_match_count,
                   COALESCE(SUM(COALESCE(planned_weight, marked_weight, 0)), 0) AS formal_weight
            FROM shipment_release_batch_matches
            WHERE release_batch_id IS NOT NULL AND release_batch_id <> ''
            GROUP BY release_batch_id
            """
        ).fetchall()
        available_columns = {row["name"] for row in db.execute("PRAGMA table_info(shipment_release_batch_matches)").fetchall()}
        select_columns = [
            "release_batch_id",
            _optional_column(available_columns, "shipment_car_no"),
            _optional_column(available_columns, "inspection_car_no"),
            _optional_column(available_columns, "latest_event_time"),
            _optional_column(available_columns, "loaded_at"),
            _optional_column(available_columns, "inspection_file"),
        ]
        detail_rows = db.execute(
            f"""
            SELECT {', '.join(select_columns)}
            FROM shipment_release_batch_matches
            WHERE release_batch_id IS NOT NULL AND release_batch_id <> ''
            ORDER BY release_batch_id, COALESCE(latest_event_time, loaded_at, ''), COALESCE(shipment_car_no, inspection_car_no, '')
            """
        ).fetchall()
    summary = {
        row["release_batch_id"]: {
            "formal_match_count": int(row["formal_match_count"] or 0),
            "formal_weight": float(row["formal_weight"] or 0),
            "car_details": [],
        }
        for row in rows
    }
    for row in detail_rows:
        item = summary.setdefault(
            row["release_batch_id"],
            {"formal_match_count": 0, "formal_weight": 0.0, "car_details": []},
        )
        item["car_details"].append(
            {
                "car_no": row["shipment_car_no"] or row["inspection_car_no"] or "",
                "time": row["latest_event_time"] or row["loaded_at"] or "",
                "inspection_file": row["inspection_file"] or "",
            }
        )
    return summary


# ── JSON data generation (new: splits data from HTML rendering) ──────────

DASHBOARD_DIR_DEFAULT: Path | None = None


def _dashboard_dir() -> Path:
    global DASHBOARD_DIR_DEFAULT
    if DASHBOARD_DIR_DEFAULT is not None:
        return DASHBOARD_DIR_DEFAULT
    # Resolve relative to the project root (parent of src/)
    candidate = Path(__file__).resolve().parents[3] / "dashboard"
    if candidate.exists():
        DASHBOARD_DIR_DEFAULT = candidate
    else:
        DASHBOARD_DIR_DEFAULT = candidate
    return DASHBOARD_DIR_DEFAULT


def generate_dispatch_board_data(
    *,
    business_db_path: str | Path,
    rail_db_path: str | Path | None = None,
    refresh_reason: str = "manual_refresh",
) -> dict[str, Any]:
    """Generate the dispatch board JSON payload from business & 95306 databases.

    Returns a dict matching ``dispatch_board_schema.md``.  This is the
    canonical data snapshot — the HTML template reads it at render time.
    """
    business_db_path = Path(business_db_path)
    rail_db_path = Path(rail_db_path) if rail_db_path else None

    with sqlite3.connect(business_db_path) as business_db:
        business_db.row_factory = sqlite3.Row
        release_rows = _fetch_release_batches(business_db)
        candidate_summary = _fetch_candidate_summary(business_db)
        audit_paths = _fetch_audit_paths(business_db)
        text_pending_rows = _fetch_text_pending_audits(business_db)
        unresolved_work_items = _fetch_unresolved_work_items(business_db)
        candidates = _fetch_inspection_candidates_json(business_db)
        reconcile_plans = _fetch_reconcile_plans_json(business_db)
        extraction_paths_by_stem = _fetch_extraction_json_paths_by_stem(business_db)

    formal_summary: dict[str, dict[str, Any]] = {}
    if rail_db_path and rail_db_path.exists():
        formal_summary = _fetch_formal_summary_read_only(rail_db_path)

    # Augment formal_summary with wagon_shipments from sop_agent.db
    _augment_formal_summary_from_sop_db(business_db_path, formal_summary)

    visible_release_ids = {str(row.get("id") or "") for row in release_rows}
    release_by_id = {str(row.get("id") or ""): row for row in release_rows}

    # ── summary ──
    active_count = sum(1 for row in release_rows if row.get("dispatch_status") == "in_progress")
    matched_candidate_count = sum(item.get("matched_candidate_count", 0) for item in candidate_summary.values())
    manual_pending_count = _manual_pending_count(candidate_summary)
    unassigned = candidate_summary.get("__unassigned__", _empty_candidate_summary())
    formal_count = sum(item.get("formal_match_count", 0) for key, item in formal_summary.items() if key in visible_release_ids)
    formal_weight = sum(float(item.get("formal_weight") or 0) for key, item in formal_summary.items() if key in visible_release_ids)

    summary = {
        "pending_total": _unresolved_total_count(unresolved_work_items),
        "pending_text_release": len(unresolved_work_items.get("text_release", [])),
        "pending_release_plan_images": len(unresolved_work_items.get("departure_plan", [])),
        "pending_inspection_assignment": len(unresolved_work_items.get("inspection_assignment", [])),
        "pending_95306_check": len(unresolved_work_items.get("inspection_validation", [])),
        "active_release_batches": active_count,
        "release_batch_total": len(release_rows),
        "matched_inspection_candidates": matched_candidate_count,
        "manual_candidate_count": manual_pending_count,
        "unassigned_candidate_count": unassigned.get("manual_pending_candidate_count", 0),
        "formal_wagon_count": formal_count,
        "formal_weight": formal_weight,
    }

    # ── pending_items ──
    pending_items = _build_pending_items_json(unresolved_work_items, release_by_id)

    # ── release_batches ──
    release_batches_json = _build_release_batches_json(
        release_rows, release_by_id, candidate_summary, formal_summary, audit_paths,
        extraction_paths_by_stem=extraction_paths_by_stem,
    )

    return {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_db": str(business_db_path.resolve()),
            "rail95306_db": str(rail_db_path.resolve()) if rail_db_path else "",
            "generator_version": "1.0.0",
            "refresh_reason": refresh_reason,
        },
        "summary": summary,
        "pending_items": pending_items,
        "release_batches": release_batches_json,
        "inspection_candidates": candidates,
        "reconcile_plans": reconcile_plans,
    }


def refresh_dispatch_board(
    reason: str = "manual_refresh",
    *,
    business_db_path: str | Path | None = None,
    rail_db_path: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Unified entry point: generate dispatch_board_data.json from business & 95306 DBs.

    Call this from any business path that changes dashboard-relevant state.
    The HTML template is a stable frontend file (committed to git) that renders the
    JSON client-side via ``fetch('./dispatch_board_data.json')``.

    This function does NOT write HTML — it only updates the JSON data file.
    Use ``write_dispatch_board_template()`` to install/update the HTML template.
    """
    if business_db_path is None:
        from ops_hub.data_agent.db import get_db_path
        business_db_path = get_db_path()
    if rail_db_path is None:
        rail_db_path = Path.home() / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
    if dashboard_dir is None:
        dashboard_dir = _dashboard_dir()

    business_db_path = Path(business_db_path)
    rail_db_path = Path(rail_db_path)
    dashboard_dir = Path(dashboard_dir)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    # Generate JSON data (HTML template renders this client-side)
    data = generate_dispatch_board_data(
        business_db_path=business_db_path,
        rail_db_path=rail_db_path if rail_db_path.exists() else None,
        refresh_reason=reason,
    )

    json_path = dashboard_dir / "dispatch_board_data.json"
    tmp_json = json_path.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_json.replace(json_path)  # atomic rename

    return {
        "json_path": str(json_path),
        "summary": data["summary"],
        "refresh_reason": reason,
    }


def write_dispatch_board_template(
    output_path: str | Path | None = None,
) -> str:
    """Write the stable dispatch board HTML template (JS + CSS only, no business data).

    This function writes the canonical HTML/JS/CSS template skeleton.  The
    template reads runtime data from ``dispatch_board_data.json`` via
    ``fetch()`` at page load — it never contains hardcoded car numbers, ship
    names, quantities, or absolute file paths.

    Call this once to install the template, or whenever the template itself
    needs updating.  It is intentionally NOT called during refresh.
    """
    if output_path is None:
        output_path = _dashboard_dir() / "dispatch_board.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Read the committed template from the dashboard directory
    template_path = _dashboard_dir() / "dispatch_board.html"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
    else:
        content = _DEFAULT_TEMPLATE

    output_path.write_text(content, encoding="utf-8")
    return str(output_path)


def ensure_dispatch_board_data(
    reason: str = "ensure_on_open_or_start",
    *,
    business_db_path: str | Path | None = None,
    rail_db_path: str | Path | None = None,
    dashboard_dir: str | Path | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """Ensure ``dispatch_board_data.json`` exists and is reasonably fresh.

    - If JSON is missing → generate it.
    - If JSON is older than *max_age_seconds* → regenerate.
    - If business DB is unavailable → write an error JSON so the frontend
      can show a clear message instead of a generic ``Failed to fetch``.

    Returns the same dict as :func:`refresh_dispatch_board`.
    """
    if dashboard_dir is None:
        dashboard_dir = _dashboard_dir()
    dashboard_dir = Path(dashboard_dir)
    json_path = dashboard_dir / "dispatch_board_data.json"

    stale = False
    if json_path.exists() and max_age_seconds is not None:
        age = time.time() - json_path.stat().st_mtime
        stale = age > max_age_seconds

    if json_path.exists() and not stale:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return {
                "json_path": str(json_path),
                "summary": data.get("summary", {}),
                "refresh_reason": data.get("meta", {}).get("refresh_reason", ""),
            }
        except Exception:
            pass  # corrupt JSON → regenerate

    # JSON missing, stale, or corrupt — regenerate
    if business_db_path is None:
        from ops_hub.data_agent.db import get_db_path
        business_db_path = get_db_path()
    business_db_path = Path(business_db_path)

    if not business_db_path.exists():
        _write_error_json(json_path, f"业务库不存在: {business_db_path}")
        return {
            "json_path": str(json_path),
            "summary": {},
            "refresh_reason": "error",
            "error": f"业务库不存在: {business_db_path}",
        }

    try:
        return refresh_dispatch_board(
            reason=reason,
            business_db_path=business_db_path,
            rail_db_path=rail_db_path,
            dashboard_dir=dashboard_dir,
        )
    except Exception as exc:
        _write_error_json(json_path, str(exc))
        return {
            "json_path": str(json_path),
            "summary": {},
            "refresh_reason": "error",
            "error": str(exc),
        }


def _write_error_json(json_path: Path, error_message: str) -> None:
    """Write a structurally valid JSON that signals an error to the frontend."""
    import time as _time
    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_db": "",
            "rail95306_db": "",
            "generator_version": "1.0.0",
            "refresh_reason": "error",
            "error": True,
            "error_message": error_message,
        },
        "summary": {},
        "pending_items": [],
        "release_batches": [],
        "inspection_candidates": [],
        "reconcile_plans": [],
    }
    tmp = json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)


def mark_pending_item_dropped(
    audit_id: str,
    *,
    reason: str = "user_dropped_from_dispatch_board",
    do_refresh: bool = True,
) -> dict[str, Any]:
    """Mark an ``image_ingestion_audit`` record as dropped (discarded_by_operator).

    The record is NOT deleted — only its ``db_action`` is set to
    ``discarded_by_operator`` and ``reason`` is updated.  After the update,
    the dashboard JSON is refreshed so the pending item disappears from the
    board on the next page load.
    """
    from ops_hub.data_agent.db import get_db_path

    db_path = get_db_path()
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        existing = db.execute(
            "SELECT id, raw_image_path, status, reason, db_action FROM image_ingestion_audit WHERE id = ?",
            (audit_id,),
        ).fetchone()

    if not existing:
        raise ValueError(f"image_ingestion_audit record not found: {audit_id}")

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE image_ingestion_audit SET db_action = 'discarded_by_operator', reason = ?, status = 'ingested' WHERE id = ?",
            (reason, audit_id),
        )
        db.commit()

    result: dict[str, Any] = {
        "audit_id": audit_id,
        "previous_status": existing["status"],
        "previous_reason": existing["reason"],
        "new_reason": reason,
    }

    if do_refresh:
        refresh_dispatch_board(reason="pending_item_dropped")

    return result


# Minimal default template used as fallback when the committed template is missing.
_DEFAULT_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Dispatch Board</title></head>
<body>
<h1>Dispatch Board — 模板未安装</h1>
<p>请运行 write_dispatch_board_template() 或从 git 仓库恢复 dispatch_board.html。</p>
</body>
</html>
"""


def _build_pending_items_json(
    items: dict[str, list[dict[str, Any]]],
    release_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    order = [
        ("text_release", "待匹配文字放货消息"),
        ("departure_plan", "待匹配出港计划/放货图片"),
        ("inspection_assignment", "待指认检装车通知单"),
        ("inspection_validation", "待95306校验装车候选"),
    ]
    for key, display_type in order:
        for item in items.get(key, []):
            candidate_lots = _candidate_lot_text(item.get("candidate_ids") or [], release_by_id)
            audit_id = str(item.get("audit_id") or "")
            result.append({
                "type": key,
                "pending_id": f"audit:{audit_id}" if audit_id else "",
                "source_table": "image_ingestion_audit",
                "source_id": audit_id,
                "display_type": display_type,
                "ship_name": item.get("ship_name") or "",
                "cargo_name": item.get("cargo_name") or "",
                "quantity": str(item.get("quantity") or ""),
                "wagon_count": 0,
                "plan_no": str(item.get("plan_id") or ""),
                "contract_no": str(item.get("contract_no") or ""),
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or ""),
                "candidate_lots": candidate_lots,
                "source_image_path": str(item.get("source_file") or ""),
                "source_json_path": str(item.get("json_path") or ""),
                "recorded_at": str(item.get("created_at") or ""),
            })
    return result


def _fetch_extraction_json_paths_by_stem(db: sqlite3.Connection) -> dict[str, str]:
    """Return { source_stem: full extraction_json_path } from message_inbox.

    Used to link release_batches.source_file_name back to the archived JSON
    that the VLM extraction produced. Old release_batches didn't store the
    archive json path; this lookup bridges them.
    """
    if not _table_exists(db, "message_inbox"):
        return {}
    rows = db.execute(
        "SELECT extraction_json_path FROM message_inbox "
        "WHERE COALESCE(extraction_json_path,'')<>''"
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        path = str(row[0] or "")
        if not path:
            continue
        stem = Path(path).stem  # e.g. "69_daf..._result"
        # Also index by the bare hash part (strip leading "<seq>_" and trailing "_result")
        out[stem] = path
        bare = stem
        if bare.endswith("_result"):
            bare = bare[:-len("_result")]
        if "_" in bare:
            bare_no_seq = bare.split("_", 1)[1]
            out[bare_no_seq] = path
        out[bare] = path
    return out


def _build_release_batches_json(
    release_rows: list[dict[str, Any]],
    release_by_id: dict[str, dict[str, Any]],
    candidate_summary: dict[str, dict[str, Any]],
    formal_summary: dict[str, dict[str, Any]],
    audit_paths: dict[str, dict[str, str]],
    extraction_paths_by_stem: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    extraction_paths_by_stem = extraction_paths_by_stem or {}
    for row in release_rows:
        release_id = str(row.get("id") or "")
        candidates = candidate_summary.get(release_id, _empty_candidate_summary())
        formal = formal_summary.get(release_id, {"formal_match_count": 0, "formal_weight": 0.0, "car_details": []})
        paths = _source_paths(row, audit_paths)
        status = str(row.get("dispatch_status") or "")
        status_label = STATUS_LABELS.get(status, status or "待人工确认")
        remaining = _remaining_text(row.get("batch_quantity"), formal.get("formal_weight"))
        candidate_lots = _candidate_lot_text(candidates.get("candidate_release_batch_ids") or [], release_by_id)
        car_details = formal.get("car_details") or []
        formal_shipments = [
            {
                "car_no": item.get("car_no") or "",
                "time": item.get("time") or "",
                "inspection_file": item.get("inspection_file") or "",
            }
            for item in car_details
        ]
        # R76: shipped_weight tracking — computed by yaml shipped_weight_rule
        shipped_w = _try_float(row.get("shipped_weight_tons")) or 0.0
        remaining_w = _try_float(row.get("remaining_weight_tons"))
        unresolved_n = int(row.get("unresolved_wagon_count") or 0)

        # Resolve extraction_json_path:
        #   1. via message_inbox.extraction_json_path indexed by source filename stem
        #   2. fallback: construct expected archive path by release_batch fields
        #      business/projects/<project>/<dest>/<ship>/lot<seq>/json/<notice_date>/<sfn>
        # The fallback handles batches whose ingest path didn't write to
        # message_inbox (manual seed / R71-R73 replay / data surgery).
        ext_json_path = "待补充"
        sfn = str(row.get("source_file_name") or "")
        if sfn:
            stem = Path(sfn).stem
            cand = extraction_paths_by_stem.get(stem) if extraction_paths_by_stem else None
            if not cand and "_" in stem and extraction_paths_by_stem:
                bare = stem.split("_", 1)[1]
                cand = extraction_paths_by_stem.get(bare)
            if cand and Path(cand).exists():
                ext_json_path = cand
            elif sfn.endswith(".json"):
                # Fallback path construction
                project_dir = str(row.get("project") or "")
                dest_dir = str(row.get("destination_station") or "")
                ship_dir = str(row.get("ship_name") or "")
                seq_dir = str(row.get("batch_sequence") or "")
                date_dir = str(row.get("notice_date") or "")
                if all([project_dir, dest_dir, ship_dir, seq_dir, date_dir]):
                    archive_root = Path(
                        "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/"
                        "business/projects"
                    )
                    expected = (archive_root / project_dir / dest_dir / ship_dir
                                / seq_dir / "json" / date_dir / sfn)
                    if expected.exists():
                        ext_json_path = str(expected)

        result.append({
            "release_batch_id": release_id,
            "project": str(row.get("project") or ""),
            "ship_name": str(row.get("ship_name") or ""),
            "destination_station": str(row.get("destination_station") or ""),
            "batch_sequence": str(row.get("batch_sequence") or ""),
            "planned_quantity": _try_float(row.get("batch_quantity")),
            "total_planned_quantity": _try_float(row.get("total_planned_quantity")),
            "batch_date": str(row.get("batch_date") or ""),
            # 货物品类(铁矿/铁矿粉)与货物品名(印粉/麦克粉)拆两个字段
            # 品类来自出港计划通知单 cargo_info.货物名称
            # 品名来自放货货运信息文字消息,出港计划通知单上没有
            "cargo_category": str(row.get("cargo_name") or ""),
            "cargo_product_name": str(row.get("cargo_product_name") or ""),
            "cargo_name": str(row.get("cargo_product_name") or row.get("cargo_name") or ""),
            "plan_no": str(row.get("plan_id") or row.get("order_id") or row.get("order_identifier") or ""),
            "contract_no": str(row.get("contract_no") or ""),
            "status": status_label,
            "dispatch_status": str(row.get("dispatch_status") or ""),
            "actual_wagon_count": int(row.get("actual_wagon_count") or 0),
            "confirmed_received_at": str(row.get("confirmed_received_at") or ""),
            "matched_candidate_count": candidates.get("matched_candidate_count", 0),
            "manual_candidate_count": candidates.get("manual_pending_candidate_count", 0),
            "candidate_lots": candidate_lots,
            "formal_wagon_count": formal.get("formal_match_count", 0),
            "candidate_lots_count": candidates.get("manual_pending_candidate_count", 0),
            # R76 — yaml-rule-driven shipped/remaining weight(替代老 formal_weight + remaining_quantity 两列)
            "shipped_weight_tons": shipped_w,
            "remaining_weight_tons": remaining_w,
            "unresolved_wagon_count": unresolved_n,
            "shipped_weight_last_computed_at": str(row.get("shipped_weight_last_computed_at") or ""),
            "source_image_path": paths.get("image_path", "待补充"),
            "extraction_json_path": ext_json_path,
            "formal_shipments": formal_shipments,
        })
    return result


def _fetch_inspection_candidates_json(db: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(db, "inspection_ingestion_candidates"):
        return []
    rows = db.execute(
        """
        SELECT id, source_file_name, status, reason, release_batch_id, wagon_count,
               car_numbers_json, payload_json, created_at
        FROM inspection_ingestion_candidates
        ORDER BY created_at DESC
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_object(row["payload_json"])
        car_numbers = _json_array(row["car_numbers_json"])
        needs_review = str(row["status"] or "") in ("pending", "ambiguous")
        result.append({
            "candidate_id": str(row["id"] or ""),
            "project": str(payload.get("project_name") or payload.get("project") or ""),
            "ship_name": str(payload.get("ship_name") or payload.get("ship") or ""),
            "destination_station": str(payload.get("destination_station") or payload.get("station") or ""),
            "candidate_lot": str(payload.get("lot") or payload.get("batch_sequence") or ""),
            "release_batch_id": str(row["release_batch_id"] or ""),
            "source_image_path": str(row["source_file_name"] or ""),
            "source_json_path": str(payload.get("source_json_path") or payload.get("json_path") or ""),
            "parsed_car_count": int(row["wagon_count"] or 0),
            "defect_car_count": max(0, int(row["wagon_count"] or 0) - len(car_numbers)),
            "status": str(row["status"] or ""),
            "needs_review": needs_review,
            "review_reasons": str(row["reason"] or ""),
            "created_at": str(row["created_at"] or ""),
        })
    return result


def _fetch_reconcile_plans_json(db: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(db, "shipment_reconcile_plans"):
        return []
    rows = db.execute(
        """
        SELECT id, candidate_id, release_batch_id, safe_to_commit,
               planned_rows, excluded_rows, review_reasons, status, created_at, committed_at
        FROM shipment_reconcile_plans
        ORDER BY created_at DESC
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "plan_id": str(row["id"] or ""),
            "candidate_id": str(row["candidate_id"] or ""),
            "release_batch_id": str(row["release_batch_id"] or ""),
            "safe_to_commit": bool(row["safe_to_commit"]),
            "planned_rows": int(row["planned_rows"] or 0),
            "excluded_rows": int(row["excluded_rows"] or 0),
            "review_reasons": str(row["review_reasons"] or ""),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
            "committed_at": str(row["committed_at"] or ""),
        })
    return result


def _try_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _render_html_from_json(data: dict[str, Any]) -> str:
    """Render the dispatch board HTML from a JSON data dict (no DB access)."""
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    pending_items = data.get("pending_items", [])
    release_batches = data.get("release_batches", [])

    generated_at = _h(meta.get("generated_at", ""))
    source_db = _h(meta.get("source_db", ""))
    rail_db = _h(meta.get("rail95306_db", "未配置"))
    refresh_reason = _h(meta.get("refresh_reason", ""))

    # summary cards
    cards = [
        ("待落实总数（文字/出港计划/检装车）", summary.get("pending_total", 0)),
        ("待匹配文字放货消息", summary.get("pending_text_release", 0)),
        ("待匹配出港计划/放货图片", summary.get("pending_release_plan_images", 0)),
        ("待指认检装车通知单", summary.get("pending_inspection_assignment", 0)),
        ("待95306校验装车候选", summary.get("pending_95306_check", 0)),
        ("当前发运中批次数", summary.get("active_release_batches", 0)),
        ("release_batch 总数", summary.get("release_batch_total", 0)),
        ("已匹配检装车候选数（含已正式入库）", summary.get("matched_inspection_candidates", 0)),
        ("待人工匹配候选数", summary.get("manual_candidate_count", 0)),
        ("未分配待人工候选数", summary.get("unassigned_candidate_count", 0)),
        ("已正式入库车数", summary.get("formal_wagon_count", 0)),
        ("已正式入库重量", _fmt_num(summary.get("formal_weight"))),
    ]
    cards_html = "\n".join(
        f'  <div class="card"><div class="label">{_h(label)}</div><div class="value">{_h(val)}</div></div>'
        for label, val in cards
    )

    # pending_items table
    pending_rows_html = _pending_items_table_html(pending_items)

    # release_batches table (split active vs completed)
    in_progress = [r for r in release_batches if r.get("status") != "已发完"]
    completed = [r for r in release_batches if r.get("status") == "已发完"]
    active_table = _release_batch_table_html(in_progress, "发运中 release_batch 明细")
    completed_table = _release_batch_table_html(completed, "已发完 release_batch 明细")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>放货 / 发运 / 图片识别 / 匹配入库看板</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1f2937; background: #f8fafc; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ color: #64748b; margin-bottom: 20px; font-size: 13px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }}
.card .label {{ color: #64748b; font-size: 13px; }}
.card .value {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
.card .error {{ color: #dc2626; }}
table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e2e8f0; margin-bottom: 16px; }}
th, td {{ border: 1px solid #e2e8f0; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #e0f2fe; position: sticky; top: 0; }}
.path {{ max-width: 260px; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.empty {{ text-align: center; color: #64748b; padding: 24px; }}
.section-title {{ margin-top: 24px; }}
.error-banner {{ background: #fee2e2; border: 1px solid #fecaca; padding: 16px; border-radius: 8px; margin-bottom: 16px; color: #991b1b; }}
</style>
</head>
<body>
<h1>放货记录 / 发运记录 / 图片下载识别 / 匹配入库看板</h1>
<div class="meta">生成时间：{generated_at}；刷新原因：{refresh_reason}；业务库：{source_db}；95306正式匹配库（只读）：{rail_db}</div>
<div id="error-banner" class="error-banner" style="display:none"></div>
<div class="cards" id="summary-cards">
{cards_html}
</div>
<h2 class="section-title">待落实消息汇总（优先处理）</h2>
<div id="pending-items-section">
{pending_rows_html}
</div>
<div id="active-release-batches-section">
{active_table}
</div>
<div id="completed-release-batches-section">
{completed_table}
</div>
<script>
// Fallback: if JSON data is embedded in a script tag, this page also works standalone.
// The primary loading path is via fetch('./dispatch_board_data.json').
(function() {{
  try {{
    var script = document.getElementById('board-data');
    if (script) {{
      // Data was already rendered server-side — nothing to do.
      console.log('Dispatch board rendered server-side.');
      return;
    }}
  }} catch(e) {{}}
  // If there's no embedded data, try to fetch it.
  fetch('./dispatch_board_data.json')
    .then(function(resp) {{
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }})
    .then(function(data) {{
      console.log('Dispatch board data loaded from JSON.');
    }})
    .catch(function(err) {{
      var banner = document.getElementById('error-banner');
      banner.style.display = 'block';
      banner.textContent = '⚠️ 无法加载 dispatch_board_data.json: ' + err.message + '。请运行 generate_dispatch_board_data.py 生成数据文件。';
    }});
}})();
</script>
</body>
</html>
"""


def _pending_items_table_html(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{_h(item.get('display_type'))}</td>"
            f"<td>{_h(item.get('ship_name'))}</td>"
            f"<td>{_h(item.get('cargo_name'))}</td>"
            f"<td>{_h(item.get('quantity'))}</td>"
            f"<td>{_h(item.get('plan_no'))}</td>"
            f"<td>{_h(item.get('contract_no'))}</td>"
            f"<td>{_h(item.get('status'))}</td>"
            f"<td>{_h(item.get('reason'))}</td>"
            f"<td>{_h(item.get('candidate_lots'))}</td>"
            f"<td class='path'>{_file_link(str(item.get('source_image_path') or ''), '来源')}</td>"
            f"<td class='path'>{_file_link(str(item.get('source_json_path') or ''), 'JSON')}</td>"
            f"<td>{_h(item.get('recorded_at'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='12' class='empty'>暂无未匹配待落实消息</td></tr>")
    return (
        "<table><thead><tr>"
        "<th>类型</th><th>船名</th><th>货名</th><th>数量/车数</th><th>计划号</th><th>合同号</th>"
        "<th>状态</th><th>原因</th><th>候选 lot</th><th>来源</th><th>JSON</th><th>记录时间</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _release_batch_table_html(batches: list[dict[str, Any]], title: str) -> str:
    rows = []
    for rb in batches:
        car_details = rb.get("formal_shipments") or []
        car_html = _car_details_html(car_details) if car_details else ""
        rows.append(
            "<tr>"
            f"<td>{_h(rb.get('project'))}</td>"
            f"<td>{_h(rb.get('ship_name'))}</td>"
            f"<td>{_h(rb.get('destination_station'))}</td>"
            f"<td>{_h(rb.get('batch_sequence'))}</td>"
            f"<td>{_fmt_num(rb.get('planned_quantity'))}</td>"
            f"<td>{_h(rb.get('batch_date'))}</td>"
            f"<td>{_h(rb.get('cargo_name'))}</td>"
            f"<td>{_h(rb.get('plan_no'))}</td>"
            f"<td>{_h(rb.get('contract_no'))}</td>"
            f"<td>{_h(rb.get('status'))}</td>"
            f"<td>{rb.get('matched_candidate_count', 0)}</td>"
            f"<td>{rb.get('manual_candidate_count', 0)}</td>"
            f"<td>{_h(rb.get('candidate_lots'))}</td>"
            f"<td>{rb.get('formal_wagon_count', 0)}</td>"
            f"<td>{_fmt_num(rb.get('formal_weight'))}</td>"
            f"<td>{car_html}</td>"
            f"<td>{_h(rb.get('remaining_quantity'))}</td>"
            f"<td class='path'>{_file_link(str(rb.get('source_image_path') or ''), '打开图片')}</td>"
            f"<td class='path'>{_file_link(str(rb.get('source_json_path') or ''), '打开JSON')}</td>"
            f"<td class='path'>{_file_link(str(rb.get('state_file_path') or ''), '打开状态')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='20' class='empty'>暂无 release_batch 数据</td></tr>")
    return (
        f'<h2 class="section-title">{_h(title)}</h2>'
        "<table><thead><tr>"
        "<th>项目</th><th>船名</th><th>到站</th><th>lot</th><th>计划吨数</th><th>批次日期</th>"
        "<th>货物品名</th><th>计划号/订单号</th><th>合同号</th><th>当前状态</th>"
        "<th>已匹配候选数</th><th>待人工候选数</th><th>候选 lot 列表</th>"
        "<th>已正式入库车数</th><th>已正式入库重量</th><th>已发运车辆明细</th>"
        "<th>理论剩余货量/车数</th><th>原始图片</th><th>JSON</th><th>状态文件</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _resolved_source_image_path(raw_image_path: Any, classified_image_path: Any = "") -> str:
    """Return an existing operator-clickable image path for audit rows.

    wx-ops-agent may store a logical raw path like ``.../2026-05/name.jpg`` while
    the actual saved image lives under the sibling preview directory. The board
    should link to the existing image instead of the non-existent logical path.
    """
    raw = str(raw_image_path or "").strip()
    classified = str(classified_image_path or "").strip()
    candidates: list[Path] = []
    if raw:
        raw_path = Path(raw)
        candidates.append(raw_path)
        candidates.append(raw_path.parent / f"{raw_path.stem}_vlm.jpg")
        candidates.append(raw_path.parent / "_preview" / raw_path.name)
    if classified:
        candidates.append(Path(classified))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return key
    return classified or raw


def _optional_column(available_columns: set[str], column_name: str) -> str:
    return column_name if column_name in available_columns else f"NULL AS {column_name}"


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_array(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _source_paths(row: dict[str, Any], audit_paths: dict[str, dict[str, str]]) -> dict[str, str]:
    source_json = _json_object(row.get("source_json"))
    source_file = str(row.get("source_file_name") or "")
    lookup_keys = [Path(source_file).name, Path(source_file).stem] if source_file else []
    audit_info: dict[str, str] = {}
    for key in lookup_keys:
        if key in audit_paths:
            audit_info = audit_paths[key]
            break
    raw_image_path = str(
        source_json.get("image_path")
        or source_json.get("raw_image_path")
        or audit_info.get("raw_image_path")
        or source_file
        or ""
    )
    classified_image_path = str(
        source_json.get("classified_image_path")
        or audit_info.get("classified_image_path")
        or ""
    )
    image_path = _resolved_source_image_path(raw_image_path, classified_image_path)
    json_path = str(
        source_json.get("json_path")
        or source_json.get("extraction_json_path")
        or audit_info.get("json_path")
        or _infer_source_artifact_path(source_file, "json")
        or ""
    )
    status_path = str(
        source_json.get("status_path")
        or source_json.get("ops_data_hub_status_path")
        or audit_info.get("status_path")
        or _infer_source_artifact_path(source_file, "status")
        or ""
    )
    return {
        "image_path": image_path or "待补充",
        "json_path": json_path or "待补充",
        "status_path": status_path or "待补充",
    }


@lru_cache(maxsize=512)
def _infer_source_artifact_path(source_file: str, artifact_kind: str) -> str:
    source = str(source_file or "").strip()
    if not source:
        return ""
    stem = Path(source).stem
    if not stem:
        return ""
    artifact_root = Path.home() / "Documents" / "bussiness-artifacts" / "wechat_images"
    if not artifact_root.exists():
        return ""
    if artifact_kind == "json":
        # Search multiple possible archive locations (most specific first)
        for pattern in [
            f"**/business/projects/**/{stem}_result.json",
            f"**/read_data/{stem}_result.json",
            f"**/extractions/**/{stem}_result.json",
        ]:
            try:
                match = next(artifact_root.glob(pattern), None)
            except OSError:
                match = None
            if match:
                return str(match)
        return ""
    elif artifact_kind == "status":
        pattern = f"**/_status/{stem}.json"
    else:
        return ""
    try:
        match = next(artifact_root.glob(pattern), None)
    except OSError:
        match = None
    return str(match) if match else ""


def _build_html(
    *,
    business_db_path: Path,
    rail_db_path: Path | None,
    release_rows: list[dict[str, Any]],
    candidate_summary: dict[str, dict[str, Any]],
    formal_summary: dict[str, dict[str, Any]],
    audit_paths: dict[str, dict[str, str]],
    text_pending_rows: list[dict[str, Any]],
    unresolved_work_items: dict[str, list[dict[str, Any]]],
) -> str:
    visible_release_ids = {str(row.get("id") or "") for row in release_rows}
    active_count = sum(1 for row in release_rows if row.get("dispatch_status") == "in_progress")
    matched_candidate_count = sum(item.get("matched_candidate_count", 0) for item in candidate_summary.values())
    manual_pending_count = _manual_pending_count(candidate_summary)
    formal_count = sum(item.get("formal_match_count", 0) for key, item in formal_summary.items() if key in visible_release_ids)
    formal_weight = sum(float(item.get("formal_weight") or 0) for key, item in formal_summary.items() if key in visible_release_ids)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    release_by_id = {str(row.get("id") or ""): row for row in release_rows}
    in_progress_rows = [row for row in release_rows if str(row.get("dispatch_status") or "") != "completed"]
    completed_rows = [row for row in release_rows if str(row.get("dispatch_status") or "") == "completed"]
    in_progress_rows_html = _release_table_rows_html(
        in_progress_rows,
        release_by_id=release_by_id,
        candidate_summary=candidate_summary,
        formal_summary=formal_summary,
        audit_paths=audit_paths,
    )
    completed_rows_html = _release_table_rows_html(
        completed_rows,
        release_by_id=release_by_id,
        candidate_summary=candidate_summary,
        formal_summary=formal_summary,
        audit_paths=audit_paths,
    )
    text_pending_html = _text_pending_table_html(text_pending_rows, release_by_id)
    unresolved_summary_html = _unresolved_summary_table_html(unresolved_work_items, release_by_id)
    unresolved_total_count = _unresolved_total_count(unresolved_work_items)
    pending_text_release_count = len(unresolved_work_items.get("text_release", []))
    pending_departure_plan_count = len(unresolved_work_items.get("departure_plan", []))
    pending_inspection_assignment_count = len(unresolved_work_items.get("inspection_assignment", []))
    pending_inspection_validation_count = len(unresolved_work_items.get("inspection_validation", []))

    unassigned = candidate_summary.get("__unassigned__", _empty_candidate_summary())
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>放货 / 发运 / 图片识别 / 匹配入库看板</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 24px; color: #1f2937; background: #f8fafc; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ color: #64748b; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }}
.card .label {{ color: #64748b; font-size: 13px; }}
.card .value {{ font-size: 28px; font-weight: 700; margin-top: 6px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e2e8f0; }}
th, td {{ border: 1px solid #e2e8f0; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #e0f2fe; position: sticky; top: 0; }}
.path {{ max-width: 260px; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.empty {{ text-align: center; color: #64748b; padding: 24px; }}
.section-title {{ margin-top: 24px; }}
</style>
</head>
<body>
<h1>放货记录 / 发运记录 / 图片下载识别 / 匹配入库看板</h1>
<div class="meta">生成时间：{_h(generated_at)}；业务库：{_h(str(business_db_path))}；95306正式匹配库（只读）：{_h(str(rail_db_path) if rail_db_path else '未配置')}</div>
<div class="cards">
  <div class="card"><div class="label">待落实总数（文字/出港计划/检装车）</div><div class="value">{unresolved_total_count}</div></div>
  <div class="card"><div class="label">待匹配文字放货消息</div><div class="value">{pending_text_release_count}</div></div>
  <div class="card"><div class="label">待匹配出港计划/放货图片</div><div class="value">{pending_departure_plan_count}</div></div>
  <div class="card"><div class="label">待指认检装车通知单</div><div class="value">{pending_inspection_assignment_count}</div></div>
  <div class="card"><div class="label">待95306校验装车候选</div><div class="value">{pending_inspection_validation_count}</div></div>
  <div class="card"><div class="label">当前发运中批次数</div><div class="value">{active_count}</div></div>
  <div class="card"><div class="label">release_batch 总数</div><div class="value">{len(release_rows)}</div></div>
  <div class="card"><div class="label">已匹配检装车候选数（含已正式入库）</div><div class="value">{matched_candidate_count}</div></div>
  <div class="card"><div class="label">待人工匹配候选数</div><div class="value">{manual_pending_count}</div></div>
  <div class="card"><div class="label">未分配待人工候选数</div><div class="value">{unassigned.get('manual_pending_candidate_count', 0)}</div></div>
  <div class="card"><div class="label">已正式入库车数</div><div class="value">{formal_count}</div></div>
  <div class="card"><div class="label">已正式入库重量</div><div class="value">{_fmt_num(formal_weight)}</div></div>
</div>
<h2 class="section-title">待落实消息汇总（优先处理）</h2>
{unresolved_summary_html}
<h2 class="section-title">正式匹配汇总 / 发运中 release_batch 明细</h2>
{_release_table_html(in_progress_rows_html)}
<h2 class="section-title">已发完 release_batch 明细</h2>
{_release_table_html(completed_rows_html)}
<h2 class="section-title">待人工匹配文字放货消息</h2>
{text_pending_html}
</body>
</html>
"""


def _release_table_html(rows_html: str) -> str:
    return f"""<table>
<thead><tr>
<th>项目</th><th>船名</th><th>到站</th><th>lot</th><th>计划吨数</th><th>批次日期</th><th>货物品类</th><th>货物品名</th><th>计划号/订单号</th><th>合同号</th><th>当前状态</th><th>已匹配候选数</th><th>待人工候选数</th><th>候选 lot 列表</th><th>已正式入库车数</th><th>已发运车辆明细</th><th>已发运重量(吨)</th><th>剩余可发运(吨)</th><th>待人工核重车数</th><th>原始图片</th><th>JSON</th>
</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>"""


def _release_table_rows_html(
    rows: list[dict[str, Any]],
    *,
    release_by_id: dict[str, dict[str, Any]],
    candidate_summary: dict[str, dict[str, Any]],
    formal_summary: dict[str, dict[str, Any]],
    audit_paths: dict[str, dict[str, str]],
) -> str:
    row_html = []
    for row in rows:
        release_id = str(row.get("id") or "")
        candidates = candidate_summary.get(release_id, _empty_candidate_summary())
        formal = formal_summary.get(release_id, {"formal_match_count": 0, "formal_weight": 0.0})
        paths = _source_paths(row, audit_paths)
        status = str(row.get("dispatch_status") or "")
        status_label = STATUS_LABELS.get(status, status or "待人工确认")
        remaining = _remaining_text(row.get("batch_quantity"), formal.get("formal_weight"))
        candidate_lots = _candidate_lot_text(candidates.get("candidate_release_batch_ids") or [], release_by_id)
        car_details_html = _car_details_html(formal.get("car_details") or [])
        row_html.append(
            "<tr>"
            f"<td>{_h(row.get('project'))}</td>"
            f"<td>{_h(row.get('ship_name'))}</td>"
            f"<td>{_h(row.get('destination_station'))}</td>"
            f"<td>{_h(row.get('batch_sequence'))}</td>"
            f"<td>{_fmt_num(row.get('batch_quantity'))}</td>"
            f"<td>{_h(row.get('batch_date'))}</td>"
            f"<td>{_h(row.get('cargo_name'))}</td>"
            f"<td>{_h(row.get('cargo_product_name'))}</td>"
            f"<td>{_h(row.get('plan_id') or row.get('order_id'))}</td>"
            f"<td>{_h(row.get('contract_no'))}</td>"
            f"<td>{_h(status_label)}</td>"
            f"<td>{candidates.get('matched_candidate_count', 0)}</td>"
            f"<td>{candidates.get('manual_pending_candidate_count', 0)}</td>"
            f"<td>{_h(candidate_lots)}</td>"
            f"<td>{formal.get('formal_match_count', 0)}</td>"
            f"<td>{car_details_html}</td>"
            f"<td>{_fmt_num(row.get('shipped_weight_tons'))}</td>"
            f"<td>{_fmt_num(row.get('remaining_weight_tons'))}</td>"
            f"<td>{int(row.get('unresolved_wagon_count') or 0)}</td>"
            f"<td class='path'>{_file_link(paths['image_path'], '打开图片')}</td>"
            f"<td class='path'>{_file_link(paths.get('json_path') or '待补充', '打开JSON')}</td>"
            "</tr>"
        )
    if not row_html:
        row_html.append("<tr><td colspan='20' class='empty'>暂无 release_batch 数据</td></tr>")
    return "".join(row_html)


def _unresolved_summary_table_html(items: dict[str, list[dict[str, Any]]], release_by_id: dict[str, dict[str, Any]]) -> str:
    rows = []
    order = ["text_release", "departure_plan", "inspection_assignment", "inspection_validation"]
    for key in order:
        for item in items.get(key, []):
            candidate_lots = _candidate_lot_text(item.get("candidate_ids") or [], release_by_id)
            rows.append(
                "<tr>"
                f"<td>{_h(item.get('kind'))}</td>"
                f"<td>{_h(item.get('ship_name'))}</td>"
                f"<td>{_h(item.get('cargo_name'))}</td>"
                f"<td>{_h(item.get('quantity'))}</td>"
                f"<td>{_h(item.get('plan_id'))}</td>"
                f"<td>{_h(item.get('contract_no'))}</td>"
                f"<td>{_h(item.get('status'))}</td>"
                f"<td>{_h(item.get('reason'))}</td>"
                f"<td>{_h(candidate_lots)}</td>"
                f"<td class='path'>{_file_link(str(item.get('source_file') or ''), '来源')}</td>"
                f"<td class='path'>{_file_link(str(item.get('json_path') or ''), 'JSON')}</td>"
                f"<td>{_h(item.get('created_at'))}</td>"
                "</tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='12' class='empty'>暂无未匹配待落实消息</td></tr>")
    return (
        "<table><thead><tr>"
        "<th>类型</th><th>船名</th><th>货名</th><th>数量/车数</th><th>计划号</th><th>合同号</th>"
        "<th>状态</th><th>原因</th><th>候选 lot</th><th>来源</th><th>JSON</th><th>记录时间</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _text_pending_table_html(rows: list[dict[str, Any]], release_by_id: dict[str, dict[str, Any]]) -> str:
    body = []
    for row in rows:
        fields = row.get("fields") or {}
        candidate_lots = _candidate_lot_text(row.get("candidate_ids") or [], release_by_id)
        body.append(
            "<tr>"
            f"<td>{_h(fields.get('船名'))}</td>"
            f"<td>{_h(fields.get('货名'))}</td>"
            f"<td>{_h(fields.get('数量'))}</td>"
            f"<td>{_h(fields.get('计划号'))}</td>"
            f"<td>{_h(fields.get('合同号'))}</td>"
            f"<td>{_h(row.get('status'))}</td>"
            f"<td>{_h(row.get('reason'))}</td>"
            f"<td>{_h(candidate_lots)}</td>"
            f"<td>{_h(row.get('created_at'))}</td>"
            "</tr>"
        )
    if not body:
        body.append("<tr><td colspan='9' class='empty'>暂无待人工匹配文字放货消息</td></tr>")
    return (
        "<table><thead><tr>"
        "<th>船名</th><th>货名</th><th>数量</th><th>计划号</th><th>合同号</th><th>状态</th><th>原因</th><th>候选 lot 列表</th><th>记录时间</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _candidate_lot_text(candidate_ids: list[str], release_by_id: dict[str, dict[str, Any]]) -> str:
    labels = []
    for candidate_id in candidate_ids:
        row = release_by_id.get(str(candidate_id), {})
        lot = str(row.get("batch_sequence") or "").strip()
        labels.append(lot or str(candidate_id))
    return "、".join(labels) if labels else ""


def _file_link(path: str, label: str) -> str:
    text = str(path or "").strip()
    if not text or text == "待补充":
        return "待补充"
    text = _resolve_existing_artifact_path(text)
    href = "file://" + quote(text)
    basename = Path(text).name or text
    return f"<a href='{_h(href)}' title='{_h(text)}'>{_h(label)}：{_h(basename)}</a>"


@lru_cache(maxsize=512)
def _resolve_existing_artifact_path(path_text: str) -> str:
    path = Path(path_text)
    if path.is_absolute() or "/" in path_text:
        return path_text
    artifact_root = Path.home() / "Documents" / "bussiness-artifacts" / "wechat_images"
    if not artifact_root.exists():
        return path_text
    try:
        match = next(artifact_root.rglob(path.name), None)
    except OSError:
        match = None
    return str(match) if match else path_text


def _car_details_html(details: list[dict[str, Any]]) -> str:
    if not details:
        return ""
    rows = []
    for item in details:
        car_no = item.get("car_no") or ""
        event_time = item.get("time") or ""
        inspection_file = item.get("inspection_file") or ""
        rows.append(
            "<tr>"
            f"<td>{_h(car_no)}</td>"
            f"<td>{_h(event_time)}</td>"
            f"<td>{_file_link(inspection_file, '检装车单') if inspection_file else ''}</td>"
            "</tr>"
        )
    return (
        f"<details><summary>查看 {len(details)} 车</summary>"
        "<table class='car-table'><thead><tr><th>车号</th><th>时间</th><th>检装车通知单</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></details>"
    )


def _remaining_text(batch_quantity: Any, formal_weight: Any) -> str:
    if batch_quantity is None or formal_weight is None:
        return "待计算"
    try:
        return _fmt_num(float(batch_quantity) - float(formal_weight))
    except Exception:
        return "待计算"


def _fmt_num(value: Any) -> str:
    if value is None or value == "":
        return "待计算"
    try:
        number = float(value)
    except Exception:
        return _h(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _h(value: Any) -> str:
    if value is None or value == "":
        return ""
    return escape(str(value), quote=True)
