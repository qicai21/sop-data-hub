"""message_inbox — unified message lifecycle table in sop_agent.db.

Schema, DAO, and CLI for the message_inbox table.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── DB path resolution ──────────────────────────────────────────────────

def _get_db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    # Default: <repo_root>/data/sop_agent.db
    # __file__ = .../sop-data-hub/src/ops_hub/sop/message_inbox.py
    # parents[3] = sop-data-hub/
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema ──────────────────────────────────────────────────────────────

MESSAGE_INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_inbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    source_agent    TEXT    DEFAULT 'wx-ops-agent',
    channel         TEXT    DEFAULT 'wechat',
    group_id        TEXT,
    group_name      TEXT,

    message_id      TEXT    NOT NULL,
    local_id        INTEGER,
    source_file     TEXT,
    source_file_type TEXT,
    source_file_stem TEXT,

    msg_type        TEXT,
    received_datetime TEXT,
    sender          TEXT,
    text_content    TEXT,

    raw_msg_path    TEXT,
    msg_path        TEXT,

    media_status        TEXT,
    registration_status TEXT,
    processing_status   TEXT    DEFAULT 'new',
    missing_media_path  TEXT,

    raw_standard_image_path     TEXT,
    document_type               TEXT,
    classification_status       TEXT,
    classification_label        TEXT,

    extraction_json_path        TEXT,
    business_archive_image_path TEXT,
    business_archive_json_path  TEXT,
    data_file_path              TEXT,

    is_sop_msg          INTEGER DEFAULT 0,
    sop_project_id      TEXT,
    sop_flow            TEXT,
    sop_node            TEXT,
    summary             TEXT,

    release_batch_id        TEXT,
    inspection_candidate_id TEXT,

    db_action       TEXT,
    db_record_ids   TEXT,
    content_sha256  TEXT,

    retry_count     INTEGER DEFAULT 0,
    error_message   TEXT,

    created_at      TEXT,
    updated_at      TEXT,
    last_seen_at    TEXT
);
"""

MESSAGE_INBOX_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_message_inbox_unique "
    "ON message_inbox(source_agent, group_id, message_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_message_id ON message_inbox(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_group_id ON message_inbox(group_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_processing_status ON message_inbox(processing_status);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_media_status ON message_inbox(media_status);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_sop_project ON message_inbox(sop_project_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_inbox_received_datetime ON message_inbox(received_datetime);",
]


# ── Migration ───────────────────────────────────────────────────────────

def ensure_message_inbox_schema(db_path: str | Path | None = None) -> None:
    db = _get_db_path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(MESSAGE_INBOX_SCHEMA)
    for idx_sql in MESSAGE_INBOX_INDEXES:
        conn.execute(idx_sql)
    conn.commit()
    conn.close()


# ── Converters ──────────────────────────────────────────────────────────

def message_event_to_inbox_row(
    event: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a MessageEvent into a dict suitable for message_inbox INSERT/REPLACE."""

    meta = getattr(event, "metadata", {}) or {}
    bundle = getattr(event, "raw_asset_bundle", None)
    rip = str(bundle.raw_image_path) if bundle and bundle.raw_image_path else None

    row: dict[str, Any] = {
        "source_agent": getattr(event, "source_agent", "wx-ops-agent") or "wx-ops-agent",
        "channel": getattr(event, "channel", "wechat") or "wechat",
        "group_id": getattr(event, "group_id", ""),
        "group_name": meta.get("group_name", ""),
        "message_id": getattr(event, "message_id", ""),
        "local_id": meta.get("local_id"),
        "source_file": meta.get("source_file", ""),
        "source_file_type": meta.get("source_file_type", "chat_record"),
        "source_file_stem": meta.get("source_file_stem", ""),
        "msg_type": getattr(event, "message_type", meta.get("message_type", "text")),
        "received_datetime": getattr(event, "received_at", None),
        "sender": meta.get("sender", ""),
        "text_content": getattr(event, "text", ""),
        "raw_msg_path": meta.get("msg_path", ""),
        "msg_path": meta.get("msg_path", ""),
        "media_status": meta.get("media_status", ""),
        "registration_status": (
            bundle.registration_status
            if bundle and hasattr(bundle, "registration_status")
            else meta.get("registration_status", "")
        ),
        "processing_status": meta.get("processing_status", "new"),
        "missing_media_path": meta.get("missing_media_path", ""),
        "raw_standard_image_path": rip,
        "document_type": "",
        "classification_status": "",
        "classification_label": "",
        "extraction_json_path": "",
        "business_archive_image_path": "",
        "business_archive_json_path": "",
        "data_file_path": "",
        "is_sop_msg": 0,
        "sop_project_id": "",
        "sop_flow": "",
        "sop_node": "",
        "summary": "",
        "release_batch_id": "",
        "inspection_candidate_id": "",
        "db_action": "",
        "db_record_ids": "",
        "content_sha256": "",
        "retry_count": 0,
        "error_message": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_seen_at": _now_iso(),
    }

    # Apply extra overrides (for future SOP matches, classifications, etc.)
    if extra:
        row.update(extra)

    return row


# ── DAO ─────────────────────────────────────────────────────────────────

def upsert_message_inbox_event(
    event: Any,
    *,
    db_path: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or update a message_inbox row from a MessageEvent."""
    db = _get_db_path(db_path)
    row = message_event_to_inbox_row(event, extra=extra)
    mid = row["message_id"]
    gid = row["group_id"] or ""
    sa = row["source_agent"]

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row

    conn.execute(MESSAGE_INBOX_SCHEMA)
    for idx_sql in MESSAGE_INBOX_INDEXES:
        try:
            conn.execute(idx_sql)
        except sqlite3.OperationalError:
            pass  # index may already exist

    # Check existing
    cur = conn.execute(
        "SELECT id, created_at FROM message_inbox "
        "WHERE source_agent = ? AND group_id = ? AND message_id = ?",
        (sa, gid, mid),
    )
    existing = cur.fetchone()

    if existing:
        update_cols = [
            "group_name", "local_id", "source_file", "source_file_type",
            "source_file_stem", "msg_type", "received_datetime", "sender",
            "text_content", "raw_msg_path", "msg_path",
            "media_status", "registration_status", "processing_status",
            "missing_media_path", "raw_standard_image_path",
            "updated_at", "last_seen_at",
        ]
        sets = ", ".join(f"{c} = ?" for c in update_cols)
        values = [row.get(c) for c in update_cols]
        values += [_now_iso(), _now_iso(), sa, gid, mid]
        conn.execute(
            f"UPDATE message_inbox SET {sets}, updated_at = ?, last_seen_at = ? "
            "WHERE source_agent = ? AND group_id = ? AND message_id = ?",
            values,
        )
        conn.commit()
        conn.close()
        return {
            "action": "updated",
            "id": existing["id"],
            "message_id": mid,
            "created_at": existing["created_at"],
        }

    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    cols = ", ".join(columns)
    values = [row.get(c) for c in columns]
    conn.execute(f"INSERT INTO message_inbox ({cols}) VALUES ({placeholders})", values)
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "action": "inserted",
        "id": new_id,
        "message_id": mid,
    }


def get_message_inbox_by_message_id(
    message_id: str,
    *,
    db_path: str | Path | None = None,
    group_id: str | None = None,
) -> dict[str, Any] | None:
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if group_id:
        cur = conn.execute(
            "SELECT * FROM message_inbox WHERE message_id = ? AND group_id = ?",
            (message_id, group_id),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM message_inbox WHERE message_id = ? ORDER BY id DESC LIMIT 1",
            (message_id,),
        )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_message_inbox(
    *,
    db_path: str | Path | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db = _get_db_path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if status:
        cur = conn.execute(
            "SELECT id, message_id, group_name, msg_type, processing_status, "
            "media_status, registration_status, received_datetime, last_seen_at "
            "FROM message_inbox WHERE processing_status = ? "
            "ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
    else:
        cur = conn.execute(
            "SELECT id, message_id, group_name, msg_type, processing_status, "
            "media_status, registration_status, received_datetime, last_seen_at "
            "FROM message_inbox ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ─────────────────────────────────────────────────────────────────

def _cli_ingest_message(message_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Ingest a single message by replaying it through source_watcher and upserting."""
    from ops_hub.sop.source_watcher import WxOpsSourceWatcher

    db = _get_db_path(db_path)
    ensure_message_inbox_schema(db)

    watcher = WxOpsSourceWatcher()
    for event in watcher.iter_message_events():
        if event.message_id == message_id:
            result = upsert_message_inbox_event(event, db_path=db)
            result["event_summary"] = {
                "message_id": event.message_id,
                "msg_type": event.message_type,
                "media_status": event.metadata.get("media_status"),
                "processing_status": event.metadata.get("processing_status"),
                "registration_status": (
                    event.raw_asset_bundle.registration_status
                    if event.raw_asset_bundle else ""
                ),
            }
            return result
    return {"error": f"message_id {message_id} not found"}


def _cli_self_test(db_path: str | Path | None = None) -> dict[str, Any]:
    """Run self-test: ingest wx_2206, wx_14, wx_2 and verify."""
    results = []
    for mid in ("wx_2206", "wx_14", "wx_2"):
        r = _cli_ingest_message(mid, db_path=db_path)
        results.append(r)
    # Read back
    conn = sqlite3.connect(str(_get_db_path(db_path)))
    conn.row_factory = sqlite3.Row
    count = conn.execute("SELECT COUNT(*) as n FROM message_inbox").fetchone()["n"]
    conn.close()
    return {"ingested": len(results), "total_rows": count, "results": results}


if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="message_inbox management CLI")
    p.add_argument("--init-db", action="store_true", help="Ensure message_inbox schema exists")
    p.add_argument("--ingest-message", type=str, help="Ingest a single message by message_id")
    p.add_argument("--self-test", action="store_true", help="Run self-test (wx_2206, wx_14, wx_2)")
    p.add_argument("--get", type=str, help="Get a message by message_id")
    p.add_argument("--list", action="store_true", help="List recent messages")
    p.add_argument("--limit", type=int, default=20, help="Limit for --list")
    p.add_argument("--status", type=str, help="Filter by processing_status")
    p.add_argument("--db", type=str, default=None, help="Override DB path")

    args = p.parse_args()
    db = Path(args.db) if args.db else None

    if args.init_db:
        ensure_message_inbox_schema(db)
        print(json.dumps({"action": "init_db", "ok": True}, ensure_ascii=False))
    elif args.ingest_message:
        print(json.dumps(_cli_ingest_message(args.ingest_message, db), ensure_ascii=False, indent=2))
    elif args.self_test:
        print(json.dumps(_cli_self_test(db), ensure_ascii=False, indent=2))
    elif args.get:
        row = get_message_inbox_by_message_id(args.get, db_path=db)
        if row:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "not found"}, ensure_ascii=False))
    elif args.list:
        rows = list_message_inbox(db_path=db, status=args.status, limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        p.print_help()
