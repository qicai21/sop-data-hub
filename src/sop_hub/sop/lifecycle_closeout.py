"""lifecycle 自动 closeout(#127,2026-06-07)。

text_watch_daemon 每轮调用 ``run_lifecycle_closeout(db_path)``,扫所有 active
phase 的 batch,按 95306 status_key 推进:

  loading/all_loaded/tracking/delivered  →  confirmed_received
    when 所有 wagon latest_stage_key ∈ {delivered, unloading_completed}
    (用户:95306 状态 80 即视为收货,delivered=80,unloading_completed=85)

  yaml lifecycle.mode='shipped_is_completed' (朝阳):
    all_loaded  →  closed  (发完即结算,不等 95306)

  confirmed_received → closed:暂不自动(避免归档过早),等用户口头说"归档"
                                或定时 N 天后兜底(也作为 future TODO)。

非阻塞:任何异常 catch 进 result["errors"],不影响 daemon 主循环。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sop_hub.sop import lifecycle as lc


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"

# 95306 stage_key 视为"已收货"(≥80 等价集)
_RECEIVED_STAGES: frozenset[str] = frozenset({"delivered", "unloading_completed"})

# 哪些 phase 还在 active tracking 范围,扫描候选
_ACTIVE_PHASES_TO_SCAN: tuple[str, ...] = (
    lc.LOADING, lc.ALL_LOADED, lc.TRACKING, lc.DELIVERED,
)


def _yaml_lifecycle_mode(project_id: str) -> str:
    """Return yaml project_meta.lifecycle.mode (default full_track_to_received)."""
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return "full_track_to_received"
    pm = (raw.get("project_meta") or {})
    return str((pm.get("lifecycle") or {}).get("mode") or "full_track_to_received")


def _batch_wagon_stage_summary(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    """Return {'total': N, 'received': K, 'all_received': bool}."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN latest_stage_key IN ('delivered','unloading_completed') "
        "          THEN 1 ELSE 0 END) AS received "
        "FROM wagon_shipments WHERE batch_id=?",
        (batch_id,),
    ).fetchone()
    total = int(row[0] or 0)
    received = int(row[1] or 0)
    return {
        "total": total,
        "received": received,
        "all_received": total > 0 and received == total,
    }


def run_lifecycle_closeout(
    *, db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Scan active batches, advance to confirmed_received / closed where appropriate.

    Returns counters:
      {"scanned": N, "advanced": K, "errors": [...]}
    """
    db = Path(db_path) if db_path else SOP_DB
    if not db.exists():
        return {"scanned": 0, "advanced": 0, "errors": ["db not found"]}

    from sop_hub.sop.lifecycle_transition import advance_lifecycle

    result: dict[str, Any] = {
        "scanned": 0, "advanced": 0, "skipped_pending": 0, "errors": [],
        "advances": [], "rejected": [],
    }
    placeholders = ",".join("?" * len(_ACTIVE_PHASES_TO_SCAN))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        batches = conn.execute(
            f"SELECT id, project, ship_name, batch_sequence, dispatch_status, "
            f"       dispatch_status_note "
            f"FROM release_batches WHERE dispatch_status IN ({placeholders})",
            _ACTIVE_PHASES_TO_SCAN,
        ).fetchall()
        result["scanned"] = len(batches)
        result.setdefault("held", 0)

        for b in batches:
            bid = b["id"]; project = b["project"] or ""; phase = b["dispatch_status"]
            # 手动保留:dispatch_status_note 含 HOLD 标记 → 跳过自动结算
            # (业务上还有车待发,人工压住,等补完车再手动放开)
            if "HOLD" in (b["dispatch_status_note"] or ""):
                result["held"] += 1
                continue
            mode = _yaml_lifecycle_mode(project)
            summary = _batch_wagon_stage_summary(conn, bid)

            # shipped_is_completed:all_loaded 直接 closed(发完即结算)
            if mode == "shipped_is_completed" and phase == lc.ALL_LOADED:
                r = advance_lifecycle(
                    bid, lc.CLOSED,
                    reason="yaml mode=shipped_is_completed + plan 满",
                    triggered_by="lifecycle_closeout",
                    db_path=str(db),
                )
                if r.get("action") == "advanced":
                    result["advanced"] += 1
                    result["advances"].append({"batch_id": bid, **r})
                elif r.get("action") == "rejected":
                    result["rejected"].append({"batch_id": bid, **r})
                continue

            # 默认 mode (full_track_to_received): 看 wagon 全收货才推
            if summary["all_received"]:
                r = advance_lifecycle(
                    bid, lc.CONFIRMED_RECEIVED,
                    reason=(f"95306 stage_key 全 received "
                            f"({summary['received']}/{summary['total']})"),
                    triggered_by="lifecycle_closeout",
                    db_path=str(db),
                )
                if r.get("action") == "advanced":
                    result["advanced"] += 1
                    result["advances"].append({"batch_id": bid, **r})
                elif r.get("action") == "rejected":
                    result["rejected"].append({"batch_id": bid, **r})
            else:
                result["skipped_pending"] += 1
    except Exception as exc:
        result["errors"].append(str(exc))
    finally:
        conn.close()
    return result
