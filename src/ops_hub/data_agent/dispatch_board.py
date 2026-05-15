from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
import json
from functools import lru_cache
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from ops_hub.data_agent.agent import active_business_sop_project_tokens, parse_business_text_fields


STATUS_LABELS = {
    "in_progress": "发运中",
    "active": "发运中",
    "completed": "已发完",
    "suspended": "暂停",
    "cancelled": "不发运",
    "pending": "待人工确认",
    "ambiguous": "待人工确认",
}

MANUAL_CANDIDATE_STATUSES = {"pending", "ambiguous"}


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
               batch_quantity, batch_date, cargo_name, cargo_product_name,
               dispatch_status, dispatch_status_note, source_file_name,
               source_json, updated_at, plan_id, order_id, contract_no
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
            if status == "candidate":
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
            SELECT raw_image_path, db_record_ids, status, reason, created_at
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
            SELECT raw_image_path, classified_image_path, extraction_json_path, status, reason, db_action, created_at
            FROM image_ingestion_audit
            WHERE message_type = 'image'
              AND classified_category = '出港计划通知单'
              AND NOT (status = 'ingested' AND COALESCE(db_action, '') LIKE 'release_batch%')
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
            SELECT raw_image_path, classified_image_path, extraction_json_path, db_record_ids, status, reason, created_at
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
        path_info = {
            "raw_image_path": str(row["raw_image_path"] or ""),
            "classified_image_path": str(row["classified_image_path"] or ""),
            "json_path": str(row["extraction_json_path"] or ""),
            "status_path": status_path,
        }
        for key in keys:
            paths.setdefault(key, path_info)
    return paths


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
        candidates.append(raw_path.parent / "_previews" / raw_path.name)
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
        or ""
    )
    status_path = str(
        source_json.get("status_path")
        or source_json.get("ops_data_hub_status_path")
        or audit_info.get("status_path")
        or ""
    )
    return {
        "image_path": image_path or "待补充",
        "json_path": json_path or "待补充",
        "status_path": status_path or "待补充",
    }


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
  <div class="card"><div class="label">已匹配检装车候选数 candidate</div><div class="value">{matched_candidate_count}</div></div>
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
<th>项目</th><th>船名</th><th>到站</th><th>lot</th><th>计划吨数</th><th>批次日期</th><th>货物品名</th><th>计划号/订单号</th><th>合同号</th><th>当前状态</th><th>已匹配候选数</th><th>待人工候选数</th><th>候选 lot 列表</th><th>已正式入库车数</th><th>已正式入库重量</th><th>已发运车辆明细</th><th>理论剩余货量/车数</th><th>原始图片</th><th>JSON</th><th>状态文件</th>
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
            f"<td>{_h(row.get('cargo_product_name') or row.get('cargo_name'))}</td>"
            f"<td>{_h(row.get('plan_id') or row.get('order_id'))}</td>"
            f"<td>{_h(row.get('contract_no'))}</td>"
            f"<td>{_h(status_label)}</td>"
            f"<td>{candidates.get('matched_candidate_count', 0)}</td>"
            f"<td>{candidates.get('manual_pending_candidate_count', 0)}</td>"
            f"<td>{_h(candidate_lots)}</td>"
            f"<td>{formal.get('formal_match_count', 0)}</td>"
            f"<td>{_fmt_num(formal.get('formal_weight'))}</td>"
            f"<td>{car_details_html}</td>"
            f"<td>{_h(remaining)}</td>"
            f"<td class='path'>{_file_link(paths['image_path'], '打开图片')}</td>"
            f"<td class='path'>{_file_link(paths['json_path'], '打开JSON')}</td>"
            f"<td class='path'>{_file_link(paths['status_path'], '打开状态')}</td>"
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
