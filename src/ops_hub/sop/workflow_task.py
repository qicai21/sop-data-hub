"""Local workflow-task planner for SOP monitoring matches.

This module stays local-only:
- convert message match results into workflow tasks;
- convert no-match or incomplete asset cases into todo items;
- preserve message / group / project / SOP-node links without touching runtime,
  database, wx-ops-agent, 95306, OCR execution, or report delivery.

R68: Added workflow_task DB table, task generation from matched_sop messages,
and CLI for managing executable tasks in sop_agent.db.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ops_hub.sop.monitoring_plan_matcher import MessageEvent, MessageMatchResult
from ops_hub.sop.raw_asset_bundle import RawAssetBundle, bind_raw_asset_bundle


@dataclass(frozen=True)
class WorkflowTask:
    task_id: str
    message_id: str
    group_id: str
    project_id: str
    target_sop_node: str
    watch_item: dict[str, Any]
    raw_asset_bundle: RawAssetBundle | None = None
    status: str = "planned"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "project_id": self.project_id,
            "target_sop_node": self.target_sop_node,
            "watch_item": self.watch_item,
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TodoItem:
    todo_id: str
    message_id: str
    group_id: str
    category: str
    reason: str
    raw_asset_bundle: RawAssetBundle | None = None
    suggested_action: str = ""
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "message_id": self.message_id,
            "group_id": self.group_id,
            "category": self.category,
            "reason": self.reason,
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "suggested_action": self.suggested_action,
            "status": self.status,
        }


@dataclass(frozen=True)
class WorkflowTaskQueue:
    event: MessageEvent
    raw_asset_bundle: RawAssetBundle | None
    match_result: MessageMatchResult
    workflow_tasks: list[WorkflowTask] = field(default_factory=list)
    todo_items: list[TodoItem] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": {
                "message_id": self.event.message_id,
                "channel": self.event.channel,
                "group_id": self.event.group_id,
                "source_agent": self.event.source_agent,
                "received_at": self.event.received_at,
                "message_type": self.event.message_type,
                "text": self.event.text,
                "raw_asset_bundle": self.event.raw_asset_bundle.to_dict() if self.event.raw_asset_bundle else None,
                "metadata": dict(self.event.metadata),
            },
            "raw_asset_bundle": self.raw_asset_bundle.to_dict() if self.raw_asset_bundle else None,
            "match_result": {
                "reason": self.match_result.reason,
                "matches": [
                    {
                        "group_id": match.group_id,
                        "watch_item": match.watch_item,
                        "candidate_projects": match.candidate_projects,
                        "target_sop_nodes": match.target_sop_nodes,
                        "reason": match.reason,
                    }
                    for match in self.match_result.matches
                ],
            },
            "workflow_tasks": [task.to_dict() for task in self.workflow_tasks],
            "todo_items": [todo.to_dict() for todo in self.todo_items],
            "reason": self.reason,
        }


def _task_id(message_id: str, group_id: str, project_id: str, target_sop_node: str) -> str:
    return f"{message_id}:{group_id}:{project_id}:{target_sop_node}"


def _todo_id(message_id: str, group_id: str, category: str) -> str:
    return f"{message_id}:{group_id}:{category}"


def _bundle_for_event(event: MessageEvent, raw_asset_bundle: RawAssetBundle | None) -> RawAssetBundle | None:
    if raw_asset_bundle is not None:
        return raw_asset_bundle
    return event.raw_asset_bundle


def _queued_event(event: MessageEvent, bundle: RawAssetBundle | None) -> MessageEvent:
    if bundle is None or event.raw_asset_bundle == bundle:
        return event
    return bind_raw_asset_bundle(event, bundle)


def _missing_bundle_category(bundle: RawAssetBundle | None) -> str:
    if bundle is None:
        return "missing_asset"
    if bundle.registration_status != "complete":
        return "incomplete_registration"
    return "missing_asset"


def _missing_bundle_reason(bundle: RawAssetBundle | None) -> str:
    if bundle is None:
        return "raw asset bundle is missing"
    if bundle.warnings:
        return "; ".join(bundle.warnings)
    return f"raw asset bundle registration_status={bundle.registration_status}"


def build_workflow_task_queue(
    event: MessageEvent,
    raw_asset_bundle: RawAssetBundle | None,
    match_result: MessageMatchResult,
) -> WorkflowTaskQueue:
    """Create workflow tasks for matches and todo items for no-match / incomplete cases."""

    bundle = _bundle_for_event(event, raw_asset_bundle)
    queued_event = _queued_event(event, bundle)

    workflow_tasks: list[WorkflowTask] = []
    todo_items: list[TodoItem] = []
    seen_task_keys: set[tuple[str, str, str]] = set()

    if match_result.matches:
        for match in match_result.matches:
            candidate_projects = match.candidate_projects or list(match.target_sop_nodes.keys())
            for project_id in candidate_projects:
                node_ids = list(match.target_sop_nodes.get(project_id) or [])
                if not node_ids:
                    continue
                for target_sop_node in node_ids:
                    task_key = (queued_event.message_id, project_id, target_sop_node)
                    if task_key in seen_task_keys:
                        continue
                    seen_task_keys.add(task_key)
                    workflow_tasks.append(
                        WorkflowTask(
                            task_id=_task_id(queued_event.message_id, queued_event.group_id or "", project_id, target_sop_node),
                            message_id=queued_event.message_id,
                            group_id=queued_event.group_id or "",
                            project_id=project_id,
                            target_sop_node=target_sop_node,
                            watch_item=match.watch_item,
                            raw_asset_bundle=bundle,
                            status="planned",
                            reason=match.reason,
                        )
                    )

    if not match_result.matches:
        todo_items.append(
            TodoItem(
                todo_id=_todo_id(queued_event.message_id, queued_event.group_id or "", "no_match"),
                message_id=queued_event.message_id,
                group_id=queued_event.group_id or "",
                category="no_match",
                reason=match_result.reason or f"no monitoring plan matched for group_id {queued_event.group_id}",
                raw_asset_bundle=bundle,
                suggested_action="review the message text and extend the monitoring plan or queue a manual follow-up",
                status="open",
            )
        )

    if bundle is None or bundle.registration_status != "complete":
        category = _missing_bundle_category(bundle)
        todo_items.append(
            TodoItem(
                todo_id=_todo_id(queued_event.message_id, queued_event.group_id or "", category),
                message_id=queued_event.message_id,
                group_id=queued_event.group_id or "",
                category=category,
                reason=_missing_bundle_reason(bundle),
                raw_asset_bundle=bundle,
                suggested_action="register the missing raw asset paths before retrying the planner",
                status="open",
            )
        )

    reason_parts = [part for part in [match_result.reason] if part]
    if bundle is not None and bundle.warnings:
        reason_parts.append("; ".join(bundle.warnings))
    reason = " | ".join(reason_parts)

    return WorkflowTaskQueue(
        event=queued_event,
        raw_asset_bundle=bundle,
        match_result=match_result,
        workflow_tasks=workflow_tasks,
        todo_items=todo_items,
        reason=reason,
    )


# ═══════════════════════════════════════════════════════════════════════════
# R68: workflow_task DB table — executable tasks from matched_sop messages
# ═══════════════════════════════════════════════════════════════════════════

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


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


WORKFLOW_TASK_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_task_db (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_inbox_id    INTEGER NOT NULL,
    message_id          TEXT    NOT NULL,
    project_id          TEXT    NOT NULL,
    flow_name           TEXT    NOT NULL,
    node_name           TEXT    NOT NULL,
    task_type           TEXT    NOT NULL,
    task_status         TEXT    NOT NULL DEFAULT 'pending',
    input_json          TEXT,
    output_json         TEXT,
    error_message       TEXT,
    retry_count         INTEGER DEFAULT 0,
    created_at          TEXT,
    updated_at          TEXT,
    last_run_at         TEXT,
    UNIQUE(message_inbox_id, task_type)
);
"""

WORKFLOW_TASK_DB_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_wt_db_project_id ON workflow_task_db(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_flow_name ON workflow_task_db(flow_name);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_task_status ON workflow_task_db(task_status);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_message_id ON workflow_task_db(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_wt_db_created_at ON workflow_task_db(created_at);",
]


def ensure_workflow_task_db_schema(db_path: str | Path | None = None) -> None:
    db = _get_db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(WORKFLOW_TASK_DB_SCHEMA)
    for idx_sql in WORKFLOW_TASK_DB_INDEXES:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _resolve_task_type(project_id: str, flow_name: str, node_name: str) -> str:
    if (project_id == "jilin_jingang_jinzhou"
            and flow_name == "departure_flow"
            and node_name == "detect_departure_message"):
        return "jljg_departure_text_chain"
    if project_id == "chaoyang_steel" and flow_name == "dispatch_flow":
        return "chaoyang_dispatch_context"
    if flow_name == "freight_detail_flow":
        return "freight_detail_enrichment"
    return "generic_sop_task"


def _build_input_json(row: dict[str, Any]) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "message_inbox_id": row.get("id"),
        "message_id": row.get("message_id"),
        "group_name": row.get("group_name"),
        "received_datetime": row.get("received_datetime"),
        "text_content": row.get("text_content"),
        "sop_project_id": row.get("sop_project_id"),
        "sop_flow": row.get("sop_flow"),
        "sop_node": row.get("sop_node"),
        "summary": row.get("summary"),
        "media_status": row.get("media_status"),
        "source_file": row.get("source_file"),
    }
    return {k: v for k, v in inp.items() if v is not None}


def create_task_from_message_inbox(
    message_inbox_id: int,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    ensure_workflow_task_db_schema(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM message_inbox WHERE id = ?", (message_inbox_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"message_inbox_id {message_inbox_id} not found"}

    row_dict = dict(row)
    project_id = row_dict.get("sop_project_id") or ""
    flow_name = row_dict.get("sop_flow") or ""
    node_name = row_dict.get("sop_node") or ""
    task_type = _resolve_task_type(project_id, flow_name, node_name)
    input_json = _build_input_json(row_dict)
    now = _now_iso()

    existing = conn.execute(
        "SELECT id, task_status, created_at FROM workflow_task_db "
        "WHERE message_inbox_id = ? AND task_type = ?",
        (message_inbox_id, task_type),
    ).fetchone()

    if existing:
        conn.close()
        return {
            "action": "skipped", "reason": "duplicate",
            "id": existing["id"], "message_inbox_id": message_inbox_id,
            "task_type": task_type, "task_status": existing["task_status"],
        }

    conn.execute(
        """INSERT INTO workflow_task_db
           (message_inbox_id, message_id, project_id, flow_name, node_name,
            task_type, task_status, input_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (message_inbox_id, row_dict.get("message_id", ""), project_id,
         flow_name, node_name, task_type,
         json.dumps(input_json, ensure_ascii=False), now, now),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "action": "created", "id": new_id,
        "message_inbox_id": message_inbox_id,
        "message_id": row_dict.get("message_id"),
        "task_type": task_type, "task_status": "pending",
    }


def create_tasks_for_matched_messages(
    *, db_path: str | Path | None = None, limit: int | None = None,
) -> dict[str, Any]:
    db = _get_db_path(db_path)
    ensure_workflow_task_db_schema(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    query = (
        "SELECT id FROM message_inbox "
        "WHERE processing_status = 'matched_sop' AND is_sop_msg = 1 ORDER BY id"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    conn.close()

    created, skipped, errors = [], [], []
    for row in rows:
        mid = row["id"]
        try:
            result = create_task_from_message_inbox(mid, db_path=db)
            if result.get("action") == "created":
                created.append(result)
            else:
                skipped.append(result)
        except Exception as exc:
            errors.append({"message_inbox_id": mid, "error": str(exc)})
    return {
        "created_count": len(created), "skipped_count": len(skipped),
        "error_count": len(errors), "created": created, "skipped": skipped,
        "errors": errors,
    }


def get_workflow_task_db_by_message_id(
    message_id: str, *, db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_task_db WHERE message_id = ? ORDER BY id",
        (message_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_workflow_task_db(
    *, task_status: str | None = None, limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if task_status:
        rows = conn.execute(
            "SELECT id, message_id, project_id, flow_name, node_name, "
            "task_type, task_status, created_at "
            "FROM workflow_task_db WHERE task_status = ? ORDER BY id DESC LIMIT ?",
            (task_status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, message_id, project_id, flow_name, node_name, "
            "task_type, task_status, created_at "
            "FROM workflow_task_db ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="workflow_task DB management CLI")
    p.add_argument("--init-db", action="store_true")
    p.add_argument("--create-for-matched", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--get-message", type=str)
    p.add_argument("--list", action="store_true")
    p.add_argument("--status", type=str)
    p.add_argument("--db", type=str, default=None)

    args = p.parse_args()
    _db = Path(args.db) if args.db else None

    if args.init_db:
        ensure_workflow_task_db_schema(_db)
        print(json.dumps({"action": "init_db", "ok": True}, ensure_ascii=False))
    elif args.create_for_matched:
        result = create_tasks_for_matched_messages(db_path=_db, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.get_message:
        tasks = get_workflow_task_db_by_message_id(args.get_message, db_path=_db)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    elif args.list:
        tasks = list_workflow_task_db(task_status=args.status, limit=args.limit or 20, db_path=_db)
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        p.print_help()
