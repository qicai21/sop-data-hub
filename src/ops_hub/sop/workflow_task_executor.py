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
    from ops_hub.sop.executor_runner import (
        run_departure_executor_chain,
        ExecutionPreview,
    )
    from ops_hub.sop.monitoring_plan_matcher import MessageEvent

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

        from ops_hub.sop.external_action_log import plan_jljg_external_actions
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
            from ops_hub.sop.external_action_log import mark_external_action_executed
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
                from ops_hub.sop.external_action_log import build_idempotency_key
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
