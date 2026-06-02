"""R69: workflow_task_db → executor_runner bridge.

Replaces the old live_service hard-coded "四平" trigger with
task-driven execution: message_inbox → workflow_task_db → executor_runner.

For jljg_departure_text_chain:
  1. Read input_json → reconstruct MessageEvent
  2. Call run_departure_executor_chain (dry-run by default)
  3. Capture ExecutionPreview → output_json
  4. Write back: task_status, output_json/error_message, last_run_at, retry_count
  5. Update message_inbox.processing_status

Other task_types: marked skipped/not_implemented for now.
"""

from __future__ import annotations

import json
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── DB path ──────────────────────────────────────────────────────────────

def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    import os
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = REPO_ROOT / "runtime"

# ── Task status conventions ──────────────────────────────────────────────

STATUSES = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "SKIPPED": "skipped",
}

MESSAGE_INBOX_STATUS_MAP = {
    "succeeded": "task_succeeded",
    "failed": "task_failed",
    "skipped": "task_skipped",
}


# ── DB helpers ───────────────────────────────────────────────────────────

def _mark_task(db_path: Path, task_id: int, status: str, **extra) -> None:
    conn = sqlite3.connect(str(db_path))
    now = _now_iso()
    sets = ["task_status = ?", "updated_at = ?"]
    values = [status, now]

    if status == STATUSES["RUNNING"]:
        sets.append("last_run_at = ?")
        values.append(now)

    for key in ("output_json", "error_message"):
        if key in extra:
            sets.append(f"{key} = ?")
            values.append(extra[key])

    if "retry_count" in extra:
        sets.append("retry_count = ?")
        values.append(extra["retry_count"])

    values.append(str(task_id))
    conn.execute(
        f"UPDATE workflow_task_db SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def _update_message_inbox_status(
    db_path: Path, message_id: str, status: str,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE message_inbox SET processing_status = ?, updated_at = ? WHERE message_id = ?",
        (status, _now_iso(), message_id),
    )
    conn.commit()
    conn.close()


# ── Core execution ───────────────────────────────────────────────────────

def run_workflow_task(
    task_id: int,
    *,
    db_path: str | Path | None = None,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Execute a single workflow_task by id.

    Returns: {task_id, task_type, action, status, output_json, error_message}
    """
    db = _get_db_path(db_path)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM workflow_task_db WHERE id = ?", (task_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"task_id": task_id, "error": "not found"}

    rd = dict(row)
    task_type = rd["task_type"]
    task_status = rd["task_status"]
    message_id = rd["message_id"]
    input_json = json.loads(rd["input_json"]) if rd.get("input_json") else {}
    conn.close()

    # Idempotent: skip already terminal tasks unless --force
    if task_status in ("succeeded", "failed") and not force:
        return {
            "task_id": task_id,
            "task_type": task_type,
            "action": "skipped",
            "reason": f"already {task_status}",
            "status": task_status,
        }

    # Mark running
    _mark_task(db, task_id, STATUSES["RUNNING"])

    # ── Dispatch by task_type ─────────────────────────────────────────
    try:
        if task_type == "jljg_departure_text_chain":
            result = _execute_jljg_departure(input_json, message_id, db_path=db, apply=apply, task_id=task_id)
        elif task_type == "create_release_batch":
            result = _execute_create_release_batch(input_json, message_id, db_path=db, apply=apply)
        elif task_type == "chaoyang_inspection_chain":
            result = _execute_chaoyang_inspection_chain(
                input_json, message_id, db_path=db, apply=apply, task_id=task_id,
            )
        elif task_type in ("chaoyang_dispatch_context", "freight_detail_enrichment", "generic_sop_task"):
            result = {
                "action": "skipped",
                "status": "skipped",
                "output_json": {
                    "reason": f"task_type {task_type} not implemented (R69)",
                    "not_implemented": True,
                },
            }
        else:
            result = {
                "action": "failed",
                "status": "failed",
                "error_message": f"unknown task_type {task_type}",
            }
    except Exception as exc:
        result = {
            "action": "failed",
            "status": "failed",
            "error_message": f"{exc}\n{traceback.format_exc()[-500:]}",
        }

    # Write back
    status = result["status"]
    output = result.get("output_json")
    error = result.get("error_message")

    _mark_task(
        db, task_id, status,
        output_json=json.dumps(output, ensure_ascii=False) if output else None,
        error_message=error,
        retry_count=(rd.get("retry_count") or 0) + 1,
    )

    # Update message_inbox
    mi_status = MESSAGE_INBOX_STATUS_MAP.get(status, "task_failed")
    _update_message_inbox_status(db, message_id, mi_status)

    return {
        "task_id": task_id,
        "task_type": task_type,
        "action": result["action"],
        "status": status,
        "output_json": output,
        "error_message": error,
    }


def _execute_jljg_departure(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    apply: bool,
    task_id: int = 0,
) -> dict[str, Any]:
    """Execute the jilin_jingang departure text chain."""
    from sop_hub.sop.executor_runner import (
        run_departure_executor_chain,
        ExecutionPreview,
    )
    from sop_hub.sop.monitoring_plan_matcher import MessageEvent

    text_content = input_json.get("text_content", "")
    group_id = input_json.get("group_name", "")
    received_at = input_json.get("received_datetime", "")

    event = MessageEvent(
        message_id=message_id,
        channel="wechat",
        group_id=group_id,
        text=text_content,
        received_at=received_at,
    )

    # ── R71: check external action idempotency before executing ──────
    effective_apply = apply
    if apply:
        try:
            import sqlite3 as _sql
            _conn = _sql.connect(str(db_path))
            executed_count = _conn.execute(
                "SELECT COUNT(*) FROM external_action_log WHERE workflow_task_id=? AND action_status='executed'",
                (task_id,),
            ).fetchone()[0]
            _conn.close()
            if executed_count > 0:
                effective_apply = False
        except Exception:
            pass  # table may not exist yet

    preview: ExecutionPreview = run_departure_executor_chain(
        event,
        runtime_root=RUNTIME_ROOT,
        db_path=str(db_path),
        apply_mode=effective_apply,
    )

    output = preview.to_dict()

    # ── R70: plan external actions with idempotency keys ──────────────
    external_actions: list[dict[str, Any]] = []
    try:
        release_batch_id = preview.release_batch_id or ""
        wagon_count = 0
        step3 = (preview.wagon_result or {})
        if isinstance(step3, dict):
            wagon_count = step3.get("planned_insert_count", 0) or step3.get("expected_car_count", 0) or 0

        from sop_hub.sop.external_action_log import plan_jljg_external_actions
        mi_id = int(input_json.get("message_inbox_id") or 0)
        external_actions = plan_jljg_external_actions(
            db_path=str(db_path),
            workflow_task_id=task_id,
            message_inbox_id=mi_id,
            message_id=message_id,
            release_batch_id=release_batch_id,
            wagon_count=wagon_count,
            ship_name=preview.release_batch_ship or "",
            apply_mode=effective_apply,
        )
        output["external_actions"] = {
            "planned": len([a for a in external_actions if a.get("action") == "created"]),
            "skipped_duplicate": len([a for a in external_actions if a.get("action") == "skipped"]),
            "effective_apply": effective_apply,
            "actions": external_actions,
        }
    except Exception as exc:
        output["external_actions_error"] = str(exc)

    # ── R71: mark external actions as executed if chain ran with apply ─
    if effective_apply and not preview.skipped_reason and not preview.error:
        try:
            from sop_hub.sop.external_action_log import mark_external_action_executed
            steps = preview.to_dict().get("steps", {})
            # Generate same keys to match
            rb_id = preview.release_batch_id or ""
            wc = wagon_count
            for atype, resp_key, resp_data in [
                ("generate_shipping_excel", "4_departure_excel",
                 {"excel_path": steps.get("4_departure_excel", {}).get("path", ""),
                  "rows": steps.get("4_departure_excel", {}).get("rows", 0)}),
                ("factory_upload_submit", "5_factory_upload",
                 {"login_success": steps.get("5_factory_upload", {}).get("login_success", False),
                  "payloads": steps.get("5_factory_upload", {}).get("payloads", 0),
                  "success": steps.get("5_factory_upload", {}).get("success", 0),
                  "failure": steps.get("5_factory_upload", {}).get("failure", 0),
                  "verified": steps.get("5b_factory_verify", {}).get("verified", False)}),
                ("send_shipping_excel_wechat", "6_send_excel",
                 {"sent": steps.get("6_send_excel", {}).get("sent", False),
                  "target": steps.get("6_send_excel", {}).get("target", "")}),
            ]:
                from sop_hub.sop.external_action_log import build_idempotency_key
                biz = f"{rb_id}:{wc}" if rb_id else f"fallback:{message_id}:{atype}"
                key = build_idempotency_key("jilin_jingang_jinzhou", atype, biz)
                mark_external_action_executed(key, db_path=str(db_path), response_json=resp_data)
        except Exception as exc:
            output["external_actions_mark_error"] = str(exc)

    # Determine status
    if preview.error:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": preview.error,
            "output_json": output,
        }
    elif preview.skipped_reason:
        return {
            "action": "skipped",
            "status": "skipped",
            "output_json": {
                **output,
                "skip_reason": preview.skipped_reason,
            },
        }
    else:
        return {
            "action": "succeeded",
            "status": "succeeded",
            "output_json": output,
        }


def _execute_create_release_batch(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    apply: bool,
) -> dict[str, Any]:
    """Execute create_release_batch: OCR extraction JSON → release_batches DB."""
    extraction_path = input_json.get("extraction_json_path")
    if not extraction_path:
        return {
            "action": "failed",
            "status": "failed",
            "error_message": "no extraction_json_path in input_json",
        }

    ext_path = Path(extraction_path)
    if not ext_path.exists():
        return {
            "action": "failed",
            "status": "failed",
            "error_message": f"extraction_json_path not found: {extraction_path}",
        }

    if not apply:
        # Dry-run: validate extraction JSON is parseable
        payload = json.loads(ext_path.read_text(encoding="utf-8"))
        return {
            "action": "dry_run",
            "status": "succeeded",
            "output_json": {
                "mode": "dry_run",
                "extraction_path": str(ext_path),
                "payload_keys": list(payload.keys()) if isinstance(payload, dict) else [],
                "message": "dry_run: extraction JSON found, apply=True to write DB",
            },
        }

    # Apply mode: actually ingest
    from sop_hub.data_agent.agent import BusinessDataAgent
    agent = BusinessDataAgent()
    records = agent.ingest_release_batch_file(str(ext_path))

    return {
        "action": "executed",
        "status": "succeeded",
        "output_json": {
            "batch_count": len(records),
            "batch_ids": [r.id for r in records],
            "batch_sequences": [r.batch_sequence for r in records],
            "ship_names": list({r.ship_name for r in records if r.ship_name}),
        },
    }


def _execute_chaoyang_inspection_chain(
    input_json: dict[str, Any],
    message_id: str,
    *,
    db_path: Path,
    apply: bool,
    task_id: int = 0,
) -> dict[str, Any]:
    """R78: 朝阳钢铁检装车通知单 → release_batch 匹配 → wagon_shipments → excel。

    步骤(只在 apply=True 时真正写库 / 写文件):
      1. 从 message_inbox_id 找 inspection_ingestion_candidates 行
      2. 读 VLM 抽取 JSON,过滤 defect=true 的车(排车不入 wagon_shipments)
      3. 用 match_release_batch_by_ship_destination_cargo 找匹配 batch
      4. 单一匹配 → 用车号逐一查 95306 拿 ydid,直插 wagon_shipments
      5. 多匹配/无匹配 → 把 candidate 标 pending_review,task 退 skipped
      6. wagon_shipments 写完 → 重算 shipped_weight + 生成 excel
      7. 返回 output_json 含:matched_batch_id / wagon_count / excel_path
    """
    import hashlib
    import json as _json
    import sqlite3 as _sql
    from sop_hub.sop.match_release_batch import (
        match_release_batch_by_ship_destination_cargo,
    )

    RAIL_DB = Path(
        "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
    )

    inbox_id = input_json.get("message_inbox_id")
    if not inbox_id:
        return {"action": "failed", "status": "failed",
                "error_message": "no message_inbox_id in input_json"}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        # ── 1. Find candidate ──────────────────────────────────────
        cand = conn.execute(
            "SELECT * FROM inspection_ingestion_candidates "
            "WHERE message_id = (SELECT message_id FROM message_inbox WHERE id=?) "
            "   OR id IN (SELECT inspection_candidate_id FROM message_inbox WHERE id=?) "
            "ORDER BY created_at DESC LIMIT 1",
            (inbox_id, inbox_id),
        ).fetchone()
        if not cand:
            return {"action": "failed", "status": "failed",
                    "error_message": f"no inspection candidate for inbox {inbox_id}"}
        cand_d = dict(cand)
        candidate_id = cand_d["id"]
        ship = (cand_d.get("ship_name") or "").strip()
        dest = (cand_d.get("destination") or "").strip()
        cargo = (cand_d.get("cargo_name") or "").strip()
        project_id = (cand_d.get("project_id") or "").strip()
        if not (ship and dest and project_id):
            return {"action": "failed", "status": "failed",
                    "error_message": f"candidate missing fields: "
                                     f"ship={ship!r} dest={dest!r} project={project_id!r}"}

        # ── 2. Read extraction JSON, filter defect ────────────────
        ext_path = cand_d.get("extraction_json_path")
        if not ext_path or not Path(ext_path).exists():
            return {"action": "failed", "status": "failed",
                    "error_message": f"extraction JSON not found: {ext_path}"}
        ext_data = _json.loads(Path(ext_path).read_text(encoding="utf-8"))
        all_rows = ext_data.get("rows") or []
        # 排车 / 缺陷车不入 wagon_shipments
        loading_rows = [r for r in all_rows if not r.get("defect")]
        loading_car_nos = [str(r.get("car_no") or "").strip()
                           for r in loading_rows if r.get("car_no")]
        if not loading_car_nos:
            return {"action": "failed", "status": "failed",
                    "error_message": "no non-defect car_no in extraction JSON"}

        # ── 3. Match release_batch ─────────────────────────────────
        m = match_release_batch_by_ship_destination_cargo(
            project_id=project_id, ship_name=ship,
            destination_station=dest, cargo_name=cargo, db_conn=conn,
        )
        if m.reason in ("no_open_batch", "no_match", "multiple_candidates"):
            if apply:
                conn.execute(
                    "UPDATE inspection_ingestion_candidates "
                    "SET candidate_status='pending_review', reason=?, "
                    "    updated_at=datetime('now') WHERE id=?",
                    (m.reason, candidate_id),
                )
                conn.commit()
            return {
                "action": "executed" if apply else "dry_run",
                "status": "skipped",
                "output_json": {
                    "stage": "match_release_batch",
                    "match_result": m.to_dict(),
                    "candidate_id": candidate_id,
                    "loading_car_count": len(loading_car_nos),
                    "candidate_status": "pending_review",
                },
            }

        matched_batch_id = m.matched_release_batch_id

        if not apply:
            return {
                "action": "dry_run",
                "status": "succeeded",
                "output_json": {
                    "stage": "would_proceed",
                    "matched_release_batch_id": matched_batch_id,
                    "candidate_id": candidate_id,
                    "loading_car_count": len(loading_car_nos),
                    "non_loading_car_count": len(all_rows) - len(loading_car_nos),
                    "note": "apply=True to write wagon_shipments + excel",
                },
            }

        # ── 4. Query 95306 per car_no, build wagon_shipments rows ─
        if not RAIL_DB.exists():
            return {"action": "failed", "status": "failed",
                    "error_message": f"95306 DB not found: {RAIL_DB}"}
        rail = _sql.connect(f"file:{RAIL_DB}?mode=ro", uri=True)
        rail.row_factory = _sql.Row
        try:
            inserted = 0
            no_match = []
            for cno in loading_car_nos:
                # 找朝阳西到站 + 货描含铁矿 + ticketed_at 最近的一条
                row = rail.execute("""
                    SELECT car_no, ydid, czydid, car_model, marked_weight, cargo_count,
                           cargo_name, transport_mode_code, transport_mode_name,
                           container_no_raw, container_numbers_json,
                           origin_name, destination_name, ticketed_at, departed_at,
                           arrived_at, delivered_at, status_name, latest_stage_key,
                           latest_stage_name, latest_event_time, accepted_at, loaded_at
                    FROM shipments
                    WHERE car_no=? AND destination_name=?
                      AND cargo_name LIKE '%铁矿%'
                    ORDER BY ticketed_at DESC LIMIT 1
                """, (cno, dest)).fetchone()
                if not row:
                    no_match.append(cno)
                    continue
                r = dict(row)
                wid = hashlib.sha1(
                    f"{r['ydid']}|{matched_batch_id}".encode()
                ).hexdigest()[:24]
                try:
                    conn.execute("""INSERT INTO wagon_shipments
                        (id, batch_id, car_no, ydid, czydid, car_model, marked_weight,
                         cargo_count, cargo_name, shipper_name, consignee_name,
                         origin_name, destination_name, ticketed_at, departed_at,
                         arrived_at, delivered_at, status_name, latest_stage_key,
                         latest_stage_name, latest_event_time, accepted_at, loaded_at,
                         transport_mode_code, transport_mode_name,
                         container_no, container_numbers_json,
                         project_id, ship_name, dispatch_status,
                         source_message_id, source_group_id, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                ?,?,?,?,?,datetime('now'),datetime('now'))""",
                        (wid, matched_batch_id, cno, r['ydid'], r['czydid'],
                         r['car_model'], float(r['marked_weight']) if r['marked_weight'] else None,
                         int(r['cargo_count']) if r['cargo_count'] else None,
                         r['cargo_name'], "", "",
                         r['origin_name'], r['destination_name'], r['ticketed_at'],
                         r['departed_at'], r['arrived_at'], r['delivered_at'],
                         r['status_name'], r['latest_stage_key'], r['latest_stage_name'],
                         r['latest_event_time'], r['accepted_at'], r['loaded_at'],
                         r['transport_mode_code'], r['transport_mode_name'],
                         r['container_no_raw'] or "", r['container_numbers_json'] or "",
                         project_id, ship, "completed",
                         cand_d.get("message_id") or "", ""))
                    # match 表
                    mid = hashlib.sha1(
                        f"{matched_batch_id}|{r['ydid']}".encode()
                    ).hexdigest()[:24]
                    try:
                        conn.execute("""INSERT INTO shipment_release_batch_matches
                            (id, release_batch_id, wagon_shipment_id, ydid,
                             waybill_no, wagon_no, container_no, match_source)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (mid, matched_batch_id, wid, r['ydid'],
                             "", cno, r['container_no_raw'] or "",
                             "chaoyang_inspection_chain"))
                    except _sql.IntegrityError:
                        pass
                    inserted += 1
                except _sql.IntegrityError:
                    # 同 batch 同 ydid 已存在,跳过
                    pass
        finally:
            rail.close()

        conn.commit()

        # ── 5. Recompute shipped_weight (yaml rule) ───────────────
        try:
            from sop_hub.sop.shipped_weight import compute_for_release_batch
            sw = compute_for_release_batch(matched_batch_id, db_path=str(db_path))
        except Exception as exc:
            sw = {"ok": False, "error": str(exc)}

        # ── 6. Generate excel ─────────────────────────────────────
        # excel 只导出"本次单子"的车号,不是 batch 历史累计。
        try:
            from sop_hub.sop.departure_excel import generate_departure_excel
            excel_result = generate_departure_excel(
                matched_batch_id, project_id=project_id,
                car_nos=loading_car_nos,
            )
            excel_info = {
                "path": excel_result.output_path,
                "wagon_count": excel_result.wagon_count,
                "error": excel_result.error,
            }
        except Exception as exc:
            excel_info = {"path": "", "wagon_count": 0, "error": str(exc)}

        # ── 6b. Send excel via wx-ui-bridge ─────────────────────────
        # 默认走 yaml 中的 test 模式 target(郭东北/数据单发群),不直发
        # 生产群。production 路径要 input_json 里显式给 send_mode="production"。
        send_info: dict[str, Any] = {"skipped": True}
        if excel_info.get("path") and not excel_info.get("error"):
            try:
                from sop_hub.sop.send_excel import send_to_wechat
                send_mode = str(input_json.get("send_mode") or "test")
                target = _resolve_send_target(project_id, send_mode)
                if target:
                    msg = (f"{ship} 发运 {excel_info['wagon_count']} 车 / "
                           f"{round(sw.get('shipped_weight_tons', 0), 1) if isinstance(sw, dict) else 0}t "
                           f"(batch {matched_batch_id[:8]})")
                    sr = send_to_wechat(
                        target=target,
                        message=msg,
                        file_path=excel_info["path"],
                    )
                    send_info = {
                        "skipped": False,
                        "mode": send_mode,
                        "target": target,
                        "success": sr.success,
                        "output_tail": (sr.output or "")[-300:],
                        "error": sr.error,
                    }
                else:
                    send_info = {"skipped": True,
                                 "reason": f"no target_contact configured for "
                                           f"send_{send_mode}_report in yaml"}
            except Exception as exc:
                send_info = {"skipped": False, "error": str(exc)}

        # ── 7. Mark candidate matched ─────────────────────────────
        conn.execute(
            "UPDATE inspection_ingestion_candidates "
            "SET candidate_status='matched', release_batch_id=?, "
            "    reason='auto_matched_by_chain', "
            "    updated_at=datetime('now') WHERE id=?",
            (matched_batch_id, candidate_id),
        )
        conn.commit()

        return {
            "action": "executed",
            "status": "succeeded",
            "output_json": {
                "matched_release_batch_id": matched_batch_id,
                "candidate_id": candidate_id,
                "loading_car_count": len(loading_car_nos),
                "non_loading_car_count": len(all_rows) - len(loading_car_nos),
                "wagon_shipments_inserted": inserted,
                "wagon_shipments_no_95306_match": no_match,
                "shipped_weight": sw,
                "excel": excel_info,
                "send": send_info,
            },
        }

    finally:
        conn.close()


def _resolve_send_target(project_id: str, mode: str = "test") -> str | None:
    """Read send target_contact (first) from yaml report_delivery_flow.

    Returns None if yaml has no test/production node configured.
    Modes: "test" -> send_test_report.target_contact[0]
           "production" -> send_production_report.target_contact[0] (or target_group)
    """
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    flows = (raw.get("flows") or {}).get("report_delivery_flow") or {}
    node_name = f"send_{mode}_report"
    for step in flows.get("steps") or []:
        if step.get("node") != node_name:
            continue
        contacts = step.get("target_contact") or []
        if contacts:
            return str(contacts[0])
        groups = step.get("target_group") or []
        if groups:
            return str(groups[0])
    return None


def run_pending_workflow_tasks(
    *,
    limit: int = 10,
    task_type: str | None = None,
    db_path: str | Path | None = None,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Run all pending workflow tasks, optionally filtered by type.

    Returns: {ran, succeeded, failed, skipped, results: [...]}
    """
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    if task_type:
        rows = conn.execute(
            "SELECT id FROM workflow_task_db WHERE task_status = 'pending' AND task_type = ? ORDER BY id LIMIT ?",
            (task_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM workflow_task_db WHERE task_status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    results = []
    counts = {"ran": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    for row in rows:
        r = run_workflow_task(row["id"], db_path=db, apply=apply, force=force)
        results.append(r)
        counts["ran"] += 1
        s = r.get("status", "unknown")
        if s in counts:
            counts[s] += 1

    return {**counts, "results": results}


def get_task_with_message(
    task_id: int, *, db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Get a workflow_task row joined with its message_inbox row."""
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """SELECT wt.*, mi.text_content, mi.group_name, mi.received_datetime
           FROM workflow_task_db wt
           LEFT JOIN message_inbox mi ON wt.message_inbox_id = mi.id
           WHERE wt.id = ?""",
        (task_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="workflow_task executor CLI")
    p.add_argument("--run-task", type=int, help="Execute a single task by id")
    p.add_argument("--run-pending", action="store_true", help="Execute pending tasks")
    p.add_argument("--task-type", type=str, help="Filter --run-pending by task_type")
    p.add_argument("--limit", type=int, default=10, help="Max tasks for --run-pending")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Real execution (default: dry-run)")
    p.add_argument("--force", action="store_true",
                   help="Re-run already succeeded/failed tasks")
    p.add_argument("--get", type=int, help="Get task with message_inbox join")
    p.add_argument("--db", type=str, default=None)

    args = p.parse_args()
    _db = Path(args.db) if args.db else None

    if args.run_task:
        result = run_workflow_task(
            args.run_task, db_path=_db, apply=args.apply, force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.run_pending:
        result = run_pending_workflow_tasks(
            limit=args.limit, task_type=args.task_type,
            db_path=_db, apply=args.apply, force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.get:
        task = get_task_with_message(args.get, db_path=_db)
        if task:
            print(json.dumps(task, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "not found"}, ensure_ascii=False))

    else:
        p.print_help()
