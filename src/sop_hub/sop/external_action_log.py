"""R70: external_action_log — idempotency ledger for external actions.

Tracks Excel generation, WeChat sending, and factory uploads with
unique idempotency keys to prevent duplicate execution across
replay/restart/force/retry scenarios.

Key format: project_id:action_type:business_key
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    import os
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _now_iso() -> str:
    from sop_hub.utils.time import now_iso_beijing
    return now_iso_beijing()


# ── Schema ──────────────────────────────────────────────────────────────

EXTERNAL_ACTION_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_action_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_task_id    INTEGER,
    message_inbox_id    INTEGER,
    message_id          TEXT,
    project_id          TEXT,
    action_type         TEXT    NOT NULL,
    idempotency_key     TEXT    NOT NULL,
    action_status       TEXT    NOT NULL DEFAULT 'planned',
    request_json        TEXT,
    response_json       TEXT,
    error_message       TEXT,
    artifact_path       TEXT,
    target_system       TEXT,
    target_channel      TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    executed_at         TEXT,
    UNIQUE(idempotency_key)
);
"""

EXTERNAL_ACTION_LOG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_eal_workflow_task_id ON external_action_log(workflow_task_id);",
    "CREATE INDEX IF NOT EXISTS idx_eal_message_id ON external_action_log(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_eal_action_type ON external_action_log(action_type);",
    "CREATE INDEX IF NOT EXISTS idx_eal_action_status ON external_action_log(action_status);",
    "CREATE INDEX IF NOT EXISTS idx_eal_created_at ON external_action_log(created_at);",
]


def ensure_external_action_log_schema(db_path: str | Path | None = None) -> None:
    db = _get_db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(EXTERNAL_ACTION_LOG_SCHEMA)
    for idx_sql in EXTERNAL_ACTION_LOG_INDEXES:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


# ── Idempotency key builder ─────────────────────────────────────────────

def build_idempotency_key(
    project_id: str,
    action_type: str,
    business_key: str,
) -> str:
    """Build an idempotency key.

    Format: project_id:action_type:business_key
    """
    return f"{project_id}:{action_type}:{business_key}"


# ── Business key helpers (jilin_jingang) ────────────────────────────────

def _biz_key_excel(release_batch_id: str, wagon_count: int) -> str:
    return f"{release_batch_id}:{wagon_count}"


def _biz_key_wechat(release_batch_id: str, wagon_count: int) -> str:
    return f"{release_batch_id}:{wagon_count}"


def _biz_key_factory(release_batch_id: str, wagon_count: int) -> str:
    return f"{release_batch_id}:{wagon_count}"


def _biz_key_fallback(message_id: str, step: str) -> str:
    """Fallback business key when release_batch_id is missing."""
    return f"fallback:{message_id}:{step}"


# ── DAO ─────────────────────────────────────────────────────────────────

def plan_external_action(
    *,
    db_path: str | Path | None = None,
    workflow_task_id: int | None = None,
    message_inbox_id: int | None = None,
    message_id: str = "",
    project_id: str = "",
    action_type: str,
    idempotency_key: str,
    action_status: str = "planned",
    request_json: dict[str, Any] | None = None,
    target_system: str = "",
    target_channel: str = "",
    artifact_path: str = "",
    fallback_key: bool = False,
) -> dict[str, Any]:
    """Plan an external action with an idempotency key.

    If the key already exists, returns the existing record (no duplicate insert).
    """
    db = _get_db_path(db_path)
    ensure_external_action_log_schema(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # Check existing
    existing = conn.execute(
        "SELECT id, action_status, created_at FROM external_action_log WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()

    if existing:
        conn.close()
        return {
            "action": "skipped", "reason": "duplicate_key",
            "id": existing["id"], "idempotency_key": idempotency_key,
            "action_status": existing["action_status"],
        }

    now = _now_iso()
    conn.execute(
        """INSERT INTO external_action_log
           (workflow_task_id, message_inbox_id, message_id, project_id,
            action_type, idempotency_key, action_status,
            request_json, artifact_path, target_system, target_channel,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            workflow_task_id, message_inbox_id, message_id, project_id,
            action_type, idempotency_key, action_status,
            json.dumps(request_json, ensure_ascii=False) if request_json else None,
            artifact_path, target_system, target_channel,
            now, now,
        ),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "action": "created", "id": new_id,
        "idempotency_key": idempotency_key, "action_status": action_status,
        "fallback_key": fallback_key,
    }


def mark_external_action_executed(
    idempotency_key: str,
    *,
    db_path: str | Path | None = None,
    response_json: dict[str, Any] | None = None,
    artifact_path: str = "",
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id FROM external_action_log WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if not row:
        conn.close()
        return {"error": f"key not found: {idempotency_key}"}
    now = _now_iso()
    conn.execute(
        "UPDATE external_action_log SET action_status = 'executed', "
        "response_json = ?, artifact_path = ?, executed_at = ?, updated_at = ? "
        "WHERE idempotency_key = ?",
        (json.dumps(response_json, ensure_ascii=False) if response_json else None,
         artifact_path, now, now, idempotency_key),
    )
    conn.commit()
    conn.close()
    return {"action": "marked_executed", "idempotency_key": idempotency_key}


def mark_external_action_failed(
    idempotency_key: str,
    error_message: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id FROM external_action_log WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if not row:
        conn.close()
        return {"error": f"key not found: {idempotency_key}"}
    now = _now_iso()
    conn.execute(
        "UPDATE external_action_log SET action_status = 'failed', "
        "error_message = ?, executed_at = ?, updated_at = ? "
        "WHERE idempotency_key = ?",
        (error_message, now, now, idempotency_key),
    )
    conn.commit()
    conn.close()
    return {"action": "marked_failed", "idempotency_key": idempotency_key}


def get_action_by_key(
    idempotency_key: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    db = _get_db_path(db_path)
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM external_action_log WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_external_actions(
    *,
    action_status: str | None = None,
    action_type: str | None = None,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    wheres = []
    params: list[Any] = []
    if action_status:
        wheres.append("action_status = ?")
        params.append(action_status)
    if action_type:
        wheres.append("action_type = ?")
        params.append(action_type)
    query = "SELECT id, workflow_task_id, message_id, project_id, action_type, idempotency_key, action_status, created_at FROM external_action_log"
    if wheres:
        query += " WHERE " + " AND ".join(wheres)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Bulk planner for jljg_departure_text_chain ──────────────────────────

def plan_jljg_external_actions(
    *,
    db_path: str | Path | None = None,
    workflow_task_id: int,
    message_inbox_id: int,
    message_id: str,
    release_batch_id: str,
    wagon_count: int,
    ship_name: str = "",
    apply_mode: bool = False,
) -> list[dict[str, Any]]:
    """Plan all external actions for a jilin_jingang departure chain.

    Returns a list of plan results, one per action_type.
    """
    project_id = "jilin_jingang_jinzhou"
    has_rb = bool(release_batch_id)
    status = "planned" if apply_mode else "skipped_dry_run"
    results: list[dict[str, Any]] = []

    # Common request payload
    request = {
        "release_batch_id": release_batch_id,
        "wagon_count": wagon_count,
        "ship_name": ship_name,
        "message_id": message_id,
    }

    # 1. generate_shipping_excel
    biz = (_biz_key_excel(release_batch_id, wagon_count) if has_rb
           else _biz_key_fallback(message_id, "excel"))
    key = build_idempotency_key(project_id, "generate_shipping_excel", biz)
    results.append(plan_external_action(
        db_path=db_path,
        workflow_task_id=workflow_task_id,
        message_inbox_id=message_inbox_id,
        message_id=message_id,
        project_id=project_id,
        action_type="generate_shipping_excel",
        idempotency_key=key,
        action_status=status,
        request_json=request,
        target_system="local",
        fallback_key=not has_rb,
    ))

    # 2. factory_upload_submit
    biz = (_biz_key_factory(release_batch_id, wagon_count) if has_rb
           else _biz_key_fallback(message_id, "factory"))
    key = build_idempotency_key(project_id, "factory_upload_submit", biz)
    results.append(plan_external_action(
        db_path=db_path,
        workflow_task_id=workflow_task_id,
        message_inbox_id=message_inbox_id,
        message_id=message_id,
        project_id=project_id,
        action_type="factory_upload_submit",
        idempotency_key=key,
        action_status=status,
        request_json=request,
        target_system="jilin_jingang_factory",
        fallback_key=not has_rb,
    ))

    # 3. send_shipping_excel_wechat
    biz = (_biz_key_wechat(release_batch_id, wagon_count) if has_rb
           else _biz_key_fallback(message_id, "wechat"))
    key = build_idempotency_key(project_id, "send_shipping_excel_wechat", biz)
    results.append(plan_external_action(
        db_path=db_path,
        workflow_task_id=workflow_task_id,
        message_inbox_id=message_inbox_id,
        message_id=message_id,
        project_id=project_id,
        action_type="send_shipping_excel_wechat",
        idempotency_key=key,
        action_status=status,
        request_json=request,
        target_system="wechat",
        target_channel="郭东北",
        fallback_key=not has_rb,
    ))

    return results


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="external_action_log CLI")
    p.add_argument("--init-db", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--status", type=str)
    p.add_argument("--type", type=str, dest="action_type")
    p.add_argument("--get-key", type=str)
    p.add_argument("--db", type=str, default=None)

    args = p.parse_args()
    _db = Path(args.db) if args.db else None

    if args.init_db:
        ensure_external_action_log_schema(_db)
        print(json.dumps({"action": "init_db", "ok": True}, ensure_ascii=False))
    elif args.list:
        rows = list_external_actions(
            action_status=args.status, action_type=args.action_type,
            limit=args.limit, db_path=_db,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.get_key:
        row = get_action_by_key(args.get_key, db_path=_db)
        if row:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "not found"}, ensure_ascii=False))
    else:
        p.print_help()
