"""装车通知没匹配上 batch 的兜底表(#126,2026-06-07)。

chain Step 2 _find_release_batch 加 phase 过滤后,会出现 3 种"没找到":
  - all_loaded_full      : ship+dest 找到了 batch,但全在 all_loaded 及之后阶段
                          → 业务上"这船所有 lot 都满了",该挂起等用户配新 lot 或挪票
  - ship_not_found       : ship 名在系统里完全没有 → 可能是新船,或 OCR 误识别
  - no_open_lot          : ship 有 batch 但当前没 enriched/loading 的(有可能全在
                          pending_freight,合同还没补;或全 confirmed_received)

这些情况都不抛错、不强配,而是落 release_batch_pending_match 表,dashboard 显示
待用户审。用户决议方式:
  - mark_resolved(pending_id, release_batch_id) — 指定挂到某 batch
  - mark_rejected(pending_id, note) — 标"非业务消息"或"挪给别的项目"
  - 新建 lot 后,batch 自动 enriched/loading → daemon 下一轮重试本 pending(可选)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS release_batch_pending_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    message_inbox_id INTEGER,
    group_id TEXT,
    project_id TEXT,
    ship_name TEXT,
    destination TEXT,
    car_count INTEGER,
    text_content TEXT,
    received_at TEXT,
    reason TEXT NOT NULL,                       -- all_loaded_full / ship_not_found / no_open_lot
    status TEXT NOT NULL DEFAULT 'pending'      -- pending / resolved / rejected
        CHECK(status IN ('pending', 'resolved', 'rejected')),
    candidate_batch_ids TEXT,                   -- JSON list:命中但被 phase 过滤掉的 batch_id
    resolved_to_batch_id TEXT,
    manual_note TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(message_id, group_id)                -- 同条消息只挂一次
);

CREATE INDEX IF NOT EXISTS idx_release_pending_match_status
  ON release_batch_pending_match(status, created_at);
CREATE INDEX IF NOT EXISTS idx_release_pending_match_ship
  ON release_batch_pending_match(ship_name, status);
"""


def ensure_schema(db_path: str | Path | None = None) -> None:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    for stmt in SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    conn.close()


def create_pending(
    *,
    message_id: str,
    reason: str,
    ship_name: str = "",
    destination: str = "",
    car_count: int = 0,
    text_content: str = "",
    received_at: str = "",
    group_id: str = "",
    project_id: str = "",
    message_inbox_id: int | None = None,
    candidate_batch_ids: list[str] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Insert pending row. Returns {"action": "created"|"already_exists", "id"|"existing_id": ...}."""
    import json
    ensure_schema(db_path=db_path)
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        existing = conn.execute(
            "SELECT id FROM release_batch_pending_match "
            "WHERE message_id=? AND COALESCE(group_id,'')=COALESCE(?,'')",
            (message_id, group_id),
        ).fetchone()
        if existing:
            return {"action": "already_exists", "existing_id": existing[0]}
        cur = conn.execute(
            "INSERT INTO release_batch_pending_match "
            "(message_id, message_inbox_id, group_id, project_id, ship_name, destination, "
            " car_count, text_content, received_at, reason, candidate_batch_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id, message_inbox_id, group_id, project_id, ship_name,
                destination, car_count, text_content, received_at, reason,
                json.dumps(candidate_batch_ids or [], ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"action": "created", "id": cur.lastrowid}
    finally:
        conn.close()


def list_pending(
    *, ship_name: str | None = None, db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    ensure_schema(db_path=db_path)
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if ship_name:
            rows = conn.execute(
                "SELECT * FROM release_batch_pending_match "
                "WHERE status='pending' AND ship_name=? ORDER BY created_at",
                (ship_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM release_batch_pending_match "
                "WHERE status='pending' ORDER BY created_at",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_resolved(
    pending_id: int, release_batch_id: str, *,
    manual_note: str = "", db_path: str | Path | None = None,
) -> bool:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "UPDATE release_batch_pending_match "
            "SET status='resolved', resolved_to_batch_id=?, manual_note=?, "
            "    resolved_at=datetime('now') WHERE id=? AND status='pending'",
            (release_batch_id, manual_note, pending_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_rejected(
    pending_id: int, *, manual_note: str = "", db_path: str | Path | None = None,
) -> bool:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "UPDATE release_batch_pending_match "
            "SET status='rejected', manual_note=?, resolved_at=datetime('now') "
            "WHERE id=? AND status='pending'",
            (manual_note, pending_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
