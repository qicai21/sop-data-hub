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

# 用户口径：status_name 文本亦可视为已收货（stage 空或滞后时兜底）
_RECEIVED_STATUS_NAMES: frozenset[str] = frozenset({
    "货物已交付",
    "确认收货",
    "已卸车",
    "已交付",
    "交付",
})

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


# 散粮(无 dispatch_plan)lot 自动完成的计划吨位闸:剩余 > 此值(约1.5车)视为
# "还在装",不自动推 confirmed_received。集装箱 lot 有 dispatch_plan 管满,不走此闸。
_BULK_REMAINING_TOLERANCE_T: float = 100.0


def _batch_has_dispatch_plan(conn: sqlite3.Connection, batch_id: str) -> bool:
    """该 batch 是否有 dispatch_plan 行(=集装箱按箱计划管满;无则为散粮按吨兜底)。"""
    return conn.execute(
        "SELECT 1 FROM release_batch_dispatch_plan WHERE release_batch_id=? LIMIT 1",
        (batch_id,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _received_predicate_sql(columns: set[str]) -> str:
    """SQL boolean expr: row counts as 95306-received per user口径."""
    stage_list = ",".join(f"'{s}'" for s in sorted(_RECEIVED_STAGES))
    parts = [f"latest_stage_key IN ({stage_list})"]
    if "status_name" in columns:
        names = ",".join(f"'{n}'" for n in sorted(_RECEIVED_STATUS_NAMES))
        parts.append(f"TRIM(COALESCE(status_name,'')) IN ({names})")
    return "(" + " OR ".join(parts) + ")"


def _batch_wagon_stage_summary(
    conn: sqlite3.Connection, batch_id: str, project_id: str = "",
) -> dict[str, Any]:
    """Return stage/receipt summary for closeout.

    Keys: total, received, all_received, has_container, last_ticketed,
    missing_loading_line, loading_line_complete.
    """
    # 吉林金钢已切到箱级唯一事实源。旧车级表是迁移前的审计快照，若仍将两表
    # 相加会把同一批集装箱重复计入 closeout。
    tables = (
        ("wagon_container_shipments",)
        if project_id == "jilin_jingang_jinzhou"
        else ("wagon_shipments", "wagon_container_shipments")
    )
    total = received = missing_line = 0
    last_tk = ""
    has_container = False
    for tbl in tables:
        cols = _table_columns(conn, tbl)
        if not cols or "batch_id" not in cols:
            continue
        recv_pred = _received_predicate_sql(cols)
        line_expr = (
            "SUM(CASE WHEN TRIM(COALESCE(loading_line,'')) = '' THEN 1 ELSE 0 END)"
            if "loading_line" in cols
            else "0"
        )
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {recv_pred} THEN 1 ELSE 0 END) AS received, "
                f"{line_expr} AS missing_line "
                f"FROM {tbl} WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            continue  # 表不存在(测试最小 schema)
        n = int(row[0] or 0)
        total += n
        received += int(row[1] or 0)
        missing_line += int(row[2] or 0)
        if tbl == "wagon_container_shipments" and n > 0:
            has_container = True
        # last_ticketed 单独取:老/测试 schema 可能无 ticketed_at 列,失败不影响计数
        if "ticketed_at" in cols:
            try:
                tk = conn.execute(
                    f"SELECT MAX(substr(ticketed_at,1,10)) FROM {tbl} WHERE batch_id=?",
                    (batch_id,)).fetchone()[0]
                if tk and str(tk) > last_tk:
                    last_tk = str(tk)
            except sqlite3.OperationalError:
                pass
    return {
        "total": total,
        "received": received,
        "all_received": total > 0 and received == total,
        "has_container": has_container,
        "last_ticketed": last_tk,
        "missing_loading_line": missing_line,
        "loading_line_complete": total > 0 and missing_line == 0,
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
            f"       dispatch_status_note, remaining_weight_tons, batch_quantity "
            f"FROM release_batches WHERE dispatch_status IN ({placeholders})",
            _ACTIVE_PHASES_TO_SCAN,
        ).fetchall()
        result["scanned"] = len(batches)
        result.setdefault("held", 0)
        result.setdefault("held_underfilled", 0)

        for b in batches:
            bid = b["id"]; project = b["project"] or ""; phase = b["dispatch_status"]
            # 手动保留:dispatch_status_note 含 HOLD 标记 → 跳过自动结算
            # (业务上还有车待发,人工压住,等补完车再手动放开)
            if "HOLD" in (b["dispatch_status_note"] or ""):
                result["held"] += 1
                continue
            mode = _yaml_lifecycle_mode(project)
            summary = _batch_wagon_stage_summary(conn, bid, project)

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

            # 默认 mode (full_track_to_received): 看 wagon 全收货才推。
            # 散粮闸(#issue-20260621):散粮 lot(无 dispatch_plan)按吨位增量装,
            # 趟间"当前车全交付"≠ lot 装完。计划吨位远没到的不准自动完成,否则
            # 诚信 lot02 这类(5096/17000t,30%)会被误判收货、从看板 loading 消失。
            # 集装箱 lot 有 dispatch_plan 按箱管满,不受此闸影响。
            if summary["all_received"]:
                # 集装箱批静默闸:全交付 + 最近2天无新车制票 才关。避免趟间空档
                # (上一趟箱全交付、下一趟还没装)把还在发的活跃船误关。散粮不受此闸
                # (它走下方吨位闸)。
                if summary.get("has_container") and summary.get("last_ticketed"):
                    from datetime import datetime, timedelta
                    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
                    if summary["last_ticketed"] > cutoff:
                        result["held_active"] = result.get("held_active", 0) + 1
                        continue
                rem = b["remaining_weight_tons"]
                if (rem is not None and float(rem) > _BULK_REMAINING_TOLERANCE_T
                        and not _batch_has_dispatch_plan(conn, bid)):
                    result["held_underfilled"] += 1
                    continue
                # 装车道线等必要字段：缺则停在 all_loaded/loading，不假推进交付确认
                if not summary.get("loading_line_complete", True):
                    result["held_missing_loading_line"] = (
                        result.get("held_missing_loading_line", 0) + 1
                    )
                    continue
                r = advance_lifecycle(
                    bid, lc.CONFIRMED_RECEIVED,
                    reason=(
                        f"95306 全收货 "
                        f"({summary['received']}/{summary['total']}; "
                        f"stage或status_name∈交付/确认收货/已卸车)"
                    ),
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
