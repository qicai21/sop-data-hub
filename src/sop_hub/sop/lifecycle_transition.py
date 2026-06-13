"""lifecycle 状态机推进器(#128,2026-06-07)。

chain 各步骤完成后调 ``advance_lifecycle(batch_id, target_phase, reason)``
推 release_batch 状态;非法跳转拒绝,转换历史落 lifecycle_transition_log 表
供追溯。

合法跳转(同向 + 跳级):
  pending_freight → enriched → loading → all_loaded → tracking → delivered
                                                                    ↓
                                                       confirmed_received → closed

  shipped_is_completed mode 允许 all_loaded → closed 直跳。

同向再触发同 phase 是 no-op(幂等,不报错)。回退是 admin 操作,需用
``rollback_lifecycle()`` 显式做(本模块不公开)。

调用方:
  - executor_runner.run_departure_executor_chain Step 4 后(wagon ingest 第一票)
    → loading
  - Step 4b allocate_wagons 后:看 plan 是否满 → all_loaded
  - workflow_task_executor _execute_freight_detail_enrichment 后 → enriched
  - lifecycle_closeout (#127) 扫描后 → tracking / delivered / confirmed_received
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sop_hub.sop import lifecycle as lc


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"


# ── 合法跳转 ─────────────────────────────────────────────────────────
# 每个 phase 允许跳到哪些 phase(含自身=幂等 no-op)
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    lc.PENDING_FREIGHT: frozenset({lc.PENDING_FREIGHT, lc.ENRICHED, lc.LOADING}),
    lc.ENRICHED:        frozenset({lc.ENRICHED, lc.LOADING, lc.ALL_LOADED}),
    # LOADING 允许直跳 CONFIRMED_RECEIVED:plan 未满/没触发 all_loaded 但 95306
    # 全车已交付的 batch(loading→delivered→confirmed_received 两步各自合法,
    # closeout 合并为一跳)
    lc.LOADING:         frozenset({lc.LOADING, lc.ALL_LOADED, lc.TRACKING, lc.DELIVERED,
                                    lc.CONFIRMED_RECEIVED}),
    lc.ALL_LOADED:      frozenset({lc.ALL_LOADED, lc.TRACKING, lc.DELIVERED,
                                    lc.CONFIRMED_RECEIVED, lc.CLOSED}),
    lc.TRACKING:        frozenset({lc.TRACKING, lc.DELIVERED, lc.CONFIRMED_RECEIVED, lc.CLOSED}),
    lc.DELIVERED:       frozenset({lc.DELIVERED, lc.CONFIRMED_RECEIVED, lc.CLOSED}),
    lc.CONFIRMED_RECEIVED: frozenset({lc.CONFIRMED_RECEIVED, lc.CLOSED}),
    lc.CLOSED:          frozenset({lc.CLOSED}),
}


# ── transition_log 表 schema ────────────────────────────────────────
TRANSITION_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifecycle_transition_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_batch_id TEXT NOT NULL,
    from_phase TEXT NOT NULL,
    to_phase TEXT NOT NULL,
    reason TEXT NOT NULL,
    triggered_by TEXT,            -- chain step / closeout / manual / ...
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_transition_batch
  ON lifecycle_transition_log(release_batch_id, created_at);
"""


def _ensure_log_schema(conn: sqlite3.Connection) -> None:
    for stmt in TRANSITION_LOG_SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


def advance_lifecycle(
    release_batch_id: str,
    target_phase: str,
    *,
    reason: str,
    triggered_by: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Try to advance batch lifecycle to target_phase.

    Returns dict with keys:
      - action: "advanced" / "noop" / "rejected"
      - from_phase / to_phase
      - reason / triggered_by

    幂等:to == from 时 noop。同向合法跳转执行 UPDATE + transition_log。
    非法跳转(回退或越规)→ rejected,不写 DB。
    """
    if target_phase not in lc.ALL_PHASES:
        return {"action": "rejected", "reason": f"unknown target_phase {target_phase!r}"}

    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_log_schema(conn)
        row = conn.execute(
            "SELECT dispatch_status FROM release_batches WHERE id=?",
            (release_batch_id,),
        ).fetchone()
        if not row:
            return {"action": "rejected", "reason": f"batch {release_batch_id} not found"}
        current = row["dispatch_status"] or ""
        if current == target_phase:
            return {
                "action": "noop", "from_phase": current, "to_phase": target_phase,
                "reason": "already at target",
            }
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if target_phase not in allowed:
            return {
                "action": "rejected", "from_phase": current, "to_phase": target_phase,
                "reason": f"illegal transition {current!r} → {target_phase!r}",
            }
        # 推进
        conn.execute(
            "UPDATE release_batches SET dispatch_status=?, "
            "dispatch_status_note=? || ' (' || COALESCE(dispatch_status_note,'') || ')', "
            "dispatch_status_updated_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (target_phase, f"#128 advance: {reason}", release_batch_id),
        )
        conn.execute(
            "INSERT INTO lifecycle_transition_log "
            "(release_batch_id, from_phase, to_phase, reason, triggered_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (release_batch_id, current, target_phase, reason, triggered_by),
        )
        conn.commit()
        return {
            "action": "advanced", "from_phase": current, "to_phase": target_phase,
            "reason": reason, "triggered_by": triggered_by,
        }
    finally:
        conn.close()
