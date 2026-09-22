"""Controlled single-event bridge used by the Slack Gateway.

The adapter never scans or runs pending work.  ``preflight_event`` is strictly
read-only.  ``execute_event`` replays the preflight, compares its digest, then
invokes exactly one existing workflow task without ``force``.  Completion is
reported only after the event facts, event workbook and GROUP013 file message
are independently observed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "sop-slack-event.v1"
SOP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = SOP_ROOT / "data/sop_agent.db"
DEFAULT_RAIL_DB = SOP_ROOT.parent / "rail95306-sync/runtime/95306_collection.sqlite3"
DEFAULT_GROUP013 = SOP_ROOT.parent / "wx-ops-agent/data/chat_records/数据单发群"
ALLOWED_TASK_TYPES = frozenset({"zhongtang_inspection_chain", "chaoyang_inspection_chain"})
AUTHORITATIVE_INSPECTION_GROUP = "铁晟业务工作群"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(facts: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(facts).encode("utf-8")).hexdigest()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _candidate_cars(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    payload = _json(candidate.get("payload_json"), {})
    rows = payload.get("rows") or []
    defects = [str(r.get("car_no") or "").strip() for r in rows if r.get("defect") and r.get("car_no")]
    cars = [str(r.get("car_no") or "").strip() for r in rows if r.get("car_no") and not r.get("defect")]
    if not cars:
        cars = [str(v).strip() for v in _json(candidate.get("car_numbers_json"), []) if str(v).strip()]
    return list(dict.fromkeys(cars)), list(dict.fromkeys(defects))


def _resolve_candidate(conn: sqlite3.Connection, *, candidate_id: str | None,
                       source_message_id: str | None, task_id: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
    task: dict[str, Any] | None = None
    if task_id is not None:
        row = conn.execute("SELECT * FROM workflow_task_db WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("workflow task not found")
        task = dict(row)
        source_message_id = str(task["message_id"])
    if candidate_id:
        rows = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE id=?", (candidate_id,)).fetchall()
    elif source_message_id:
        rows = conn.execute("SELECT * FROM inspection_ingestion_candidates WHERE message_id=? ORDER BY created_at DESC", (source_message_id,)).fetchall()
    else:
        raise ValueError("one exact candidate/source/task selector is required")
    if len(rows) != 1:
        raise ValueError(f"selector resolved {len(rows)} candidates; expected exactly one")
    candidate = dict(rows[0])
    if task is None:
        tasks = conn.execute(
            "SELECT * FROM workflow_task_db WHERE message_id=? AND task_type IN ('zhongtang_inspection_chain','chaoyang_inspection_chain') ORDER BY id DESC",
            (candidate["message_id"],),
        ).fetchall()
        if len(tasks) != 1:
            raise ValueError(f"candidate resolved {len(tasks)} executable tasks; expected exactly one")
        task = dict(tasks[0])
    return candidate, task


def _rail_shipments(cars: list[str], destination: str, rail_db: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not cars or not rail_db.is_file():
        return [], cars
    conn = sqlite3.connect(f"file:{rail_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" for _ in cars)
        rows = conn.execute(
            f"SELECT ydid,car_no,destination_name,cargo_name,ticketed_at,status_name,latest_stage_key "
            f"FROM shipments WHERE car_no IN ({marks}) AND destination_name=? "
            f"AND cargo_name LIKE '%铁矿%' ORDER BY ticketed_at DESC",
            [*cars, destination],
        ).fetchall()
    finally:
        conn.close()
    by_car: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_car.setdefault(str(row["car_no"]), []).append(dict(row))
    # The formal executor uses the newest matching 95306 ticket per car.  Match
    # that exact rule here; the inspection-sheet date is not the ticket date.
    unique = [values[0] for car in cars if (values := by_car.get(car, []))]
    unresolved = [car for car in cars if not by_car.get(car)]
    return unique, unresolved


def preflight_event(*, candidate_id: str | None = None, source_message_id: str | None = None,
                    task_id: int | None = None, db_path: Path = DEFAULT_DB,
                    rail_db: Path = DEFAULT_RAIL_DB) -> dict[str, Any]:
    """Build a stable, read-only event contract."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        candidate, task = _resolve_candidate(conn, candidate_id=candidate_id,
                                             source_message_id=source_message_id, task_id=task_id)
        inbox = conn.execute("SELECT * FROM message_inbox WHERE id=?", (task["message_inbox_id"],)).fetchone()
        if not inbox:
            raise ValueError("message inbox row not found")
        inbox = dict(inbox)
        payload = _json(candidate.get("payload_json"), {})
        event_date = str(((payload.get("meta") or {}).get("date") or inbox.get("received_datetime") or ""))
        import re
        m = re.search(r"(20\d{2})\D*(\d{1,2})\D*(\d{1,2})", event_date)
        event_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else str(inbox.get("received_datetime") or "")[:10]
        # Re-run the current read-only inference so lifecycle fixes affect the
        # contract without mutating the stale candidate row.
        from sop_hub.sop.infer_candidate_context import infer_candidate_context
        inferred = infer_candidate_context(payload, group_name=str(inbox.get("group_name") or ""),
                                           received_datetime=str(inbox.get("received_datetime") or ""), db_path=db_path)
        batch_id = inferred.release_batch_id or str(candidate.get("release_batch_id") or "")
        batch_row = conn.execute("SELECT * FROM release_batches WHERE id=?", (batch_id,)).fetchone()
        batch = dict(batch_row) if batch_row else {}
        cars, defects = _candidate_cars(candidate)
        destination = inferred.destination or candidate.get("destination") or batch.get("destination") or ""
        shipments, unresolved = _rail_shipments(cars, str(destination), rail_db)
        ydids = sorted(str(s["ydid"]) for s in shipments if s.get("ydid"))
        conflicts = []
        if ydids:
            marks = ",".join("?" for _ in ydids)
            conflicts = [dict(r) for r in conn.execute(
                f"SELECT ydid,batch_id,car_no FROM wagon_shipments WHERE ydid IN ({marks}) AND batch_id!=?",
                [*ydids, batch_id],
            ).fetchall()]
        try:
            actions = [dict(r) for r in conn.execute(
                "SELECT action_type,idempotency_key,action_status,executed_at FROM external_action_log WHERE message_id=? ORDER BY id",
                (candidate["message_id"],),
            ).fetchall()]
        except sqlite3.OperationalError:
            actions = []
    finally:
        conn.close()

    errors = []
    if str(inbox.get("group_name") or "") != AUTHORITATIVE_INSPECTION_GROUP:
        errors.append("source_group_not_authoritative")
    if task.get("task_type") not in ALLOWED_TASK_TYPES:
        errors.append("task_type_not_allowed")
    if not inferred.matched or not batch:
        errors.append("release_batch_not_unique")
    if batch and batch.get("dispatch_status") not in ("pending_freight", "enriched", "loading"):
        errors.append("release_batch_not_open")
    if len(cars) != int(candidate.get("wagon_count") or len(cars)):
        errors.append("candidate_count_mismatch")
    if unresolved:
        errors.append("rail_ydid_not_unique")
    if conflicts:
        errors.append("ydid_conflict")
    facts = {
        "candidate_id": candidate["id"], "source_message_id": candidate["message_id"],
        "workflow_task_id": int(task["id"]), "workflow_task_type": task["task_type"],
        "project_id": inferred.project_id or candidate.get("project_id"),
        "source_group": inbox.get("group_name"), "event_date": event_date,
        "ship_name": inferred.ship_name or candidate.get("ship_name"),
        "destination": inferred.destination or candidate.get("destination"),
        "cargo_name": inferred.cargo_name or candidate.get("cargo_name"),
        "release_batch_id": batch_id, "batch_sequence": batch.get("batch_sequence"),
        "batch_status": batch.get("dispatch_status"), "plan_number": batch.get("plan_id") or batch.get("order_identifier"),
        "unit_count": len(cars), "car_numbers": cars, "defect_cars": defects,
        "ydids": ydids, "unresolved_cars": unresolved, "ydid_conflicts": conflicts,
        "external_actions": actions,
    }
    return {"schema_version": SCHEMA_VERSION, "ready": not errors, "errors": errors,
            "facts": facts, "digest": _digest(facts)}


def _group013_attachment(filename: str, not_before: str, root: Path = DEFAULT_GROUP013) -> dict[str, Any] | None:
    for path in sorted(root.glob("*.jsonl"), reverse=True):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            row = _json(line, {})
            if row.get("msg-type") == "file" and Path(str(row.get("msg-path") or "")).name == filename and str(row.get("time") or "") >= not_before:
                return {"record_file": str(path), "time": row.get("time"), "msg_path": row.get("msg-path"), "sender": row.get("sender")}
    return None


def execute_event(token: dict[str, Any], *, db_path: Path = DEFAULT_DB, rail_db: Path = DEFAULT_RAIL_DB,
                  group013_root: Path = DEFAULT_GROUP013) -> dict[str, Any]:
    """Execute exactly one unchanged preflight. Never retries or forces."""
    now = datetime.now(timezone.utc)
    try:
        issued_at = datetime.fromisoformat(str(token["issued_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(token["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return {"status": "rejected", "stage": "token", "reason": "invalid_token_time"}
    if issued_at > now or expires_at <= now:
        return {"status": "rejected", "stage": "token", "reason": "token_expired_or_not_yet_valid"}
    expected = str(token.get("digest") or "")
    facts = token.get("facts") or {}
    current = preflight_event(candidate_id=facts.get("candidate_id"), db_path=db_path, rail_db=rail_db)
    if not expected or current["digest"] != expected:
        return {"status": "rejected", "stage": "digest_check", "reason": "preflight_changed", "current": current}
    if not current["ready"]:
        return {"status": "rejected", "stage": "preflight", "reason": "preflight_not_ready", "current": current}
    task_id = int(facts["workflow_task_id"])
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    backup_dir = SOP_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"sop_agent.before-slack-task-{task_id}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(db_path, backup_path)

    # The stale candidate context is repaired only from the signed, rechecked
    # preflight.  All subsequent writes remain in the formal task executor.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET project_id=?,ship_name=?,destination=?,cargo_name=?,release_batch_id=?,candidate_status='candidate',reason='slack_signed_preflight',updated_at=datetime('now') WHERE id=?",
            (facts["project_id"], facts["ship_name"], facts["destination"], facts["cargo_name"], facts["release_batch_id"], facts["candidate_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    from sop_hub.sop.workflow_task_executor import run_workflow_task
    result = run_workflow_task(task_id, db_path=db_path, force=False)
    output = result.get("output_json") or {}
    if result.get("status") != "succeeded":
        return {"status": "failed", "stage": "workflow_task", "result": result}
    excel = output.get("excel") or {}
    excel_path = str(output.get("excel_path") or excel.get("path") or excel.get("excel_path") or "")
    if not excel_path:
        # multi wrapper nests per-candidate output
        entries = output.get("per_candidate") or []
        for entry in entries:
            nested = entry.get("output_json") or {}
            nested_excel = nested.get("excel") or {}
            excel_path = str(nested.get("excel_path") or nested_excel.get("path") or nested_excel.get("excel_path") or "")
            if excel_path:
                break
    if not excel_path or not Path(excel_path).is_file():
        return {"status": "failed", "stage": "event_excel_verification", "result": result}
    evidence = _group013_attachment(Path(excel_path).name, started, group013_root)
    if not evidence:
        return {"status": "awaiting_evidence", "stage": "group013_attachment_readback",
                "excel_path": excel_path, "result": result}
    return {"status": "completed", "stage": "verified", "excel_path": excel_path,
            "backup_path": str(backup_path), "group013_attachment": evidence, "result": result}
