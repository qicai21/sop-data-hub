"""工厂上传结果台账 —— 记录每次上传后门户回的「门户ID」,查重 + 2 月留存。

为什么单独建库(2026-06-17 用户要求):
  收货人门户是**覆盖式**——同一(车号,箱号)删不掉却能重传,底层堆重复记录,
  而列表 API **每箱只显示 1 条**,把重复藏起来,直接干扰对方收货派车。
  我们这端没法靠列表看出重复,只能**自己记账**:每次上传后反查门户,把
  「箱号 → 门户ID(对方主键)」连同车号/订单/趟次/时间落到独立小库。
  **同一箱号攒到 ≥2 个不同门户ID = 必然发生过重复上传** → 告警。

库:data/factory_upload_ledger.db(独立于 sop_agent.db,纯审计用,可随时重建)。
留存:observed_at 超 60 天自动清(record 时顺带 prune)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_DB = REPO_ROOT / "data" / "factory_upload_ledger.db"
RETENTION_DAYS = 60  # 2 个月

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    box_no       TEXT NOT NULL,            -- 箱号
    portal_id    INTEGER,                  -- 门户ID(对方系统主键);反查不到为 NULL
    car_no       TEXT,
    order_id     TEXT,                     -- 订单标识号
    ship_name    TEXT,
    project_id   TEXT,
    trip_label   TEXT,                     -- 列序号(第N列)
    uploaded_at  TEXT NOT NULL,            -- 我方上传时刻(北京 ISO)
    observed_at  TEXT NOT NULL,            -- 反查到该门户ID的时刻
    -- 同一 (箱号,门户ID) 只记一次;门户ID 变了(重传)才会多一行
    UNIQUE(order_id, box_no, portal_id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_box ON upload_ledger(order_id, box_no);
CREATE INDEX IF NOT EXISTS idx_ledger_observed ON upload_ledger(observed_at);
"""


def _db(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else LEDGER_DB


def ensure_schema(db_path: str | Path | None = None) -> None:
    db = _db(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _now() -> str:
    from sop_hub.utils.time import now_iso_beijing
    return now_iso_beijing()


def prune_expired(db_path: str | Path | None = None, *, days: int = RETENTION_DAYS) -> int:
    """删 observed_at 超 days 天的记录。返回删除条数。"""
    from datetime import datetime, timedelta
    ensure_schema(db_path)
    try:
        cutoff = (datetime.fromisoformat(_now()) - timedelta(days=days)).isoformat()
    except Exception:
        return 0
    conn = sqlite3.connect(str(_db(db_path)))
    try:
        n = conn.execute("DELETE FROM upload_ledger WHERE observed_at < ?", (cutoff,)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def record_observations(
    rows: Iterable[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    prune: bool = True,
) -> dict[str, Any]:
    """记一批「箱号 → 门户ID」观测。

    rows 每条:{box_no, portal_id, car_no, order_id, ship_name, project_id,
               trip_label, uploaded_at}。observed_at 自动盖。
    UNIQUE(order_id,box_no,portal_id) 幂等:同箱同门户ID重复观测不增行;
    门户ID 变了(=重传)才会多出一行 → 这正是查重信号。
    """
    ensure_schema(db_path)
    now = _now()
    conn = sqlite3.connect(str(_db(db_path)))
    inserted = 0
    try:
        for r in rows:
            cur = conn.execute(
                """INSERT OR IGNORE INTO upload_ledger
                   (box_no, portal_id, car_no, order_id, ship_name,
                    project_id, trip_label, uploaded_at, observed_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(r.get("box_no") or ""), r.get("portal_id"),
                 r.get("car_no"), r.get("order_id"), r.get("ship_name"),
                 r.get("project_id"), r.get("trip_label"),
                 r.get("uploaded_at") or now, now),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    pruned = prune_expired(db_path) if prune else 0
    return {"inserted": inserted, "pruned": pruned}


def find_duplicates(
    order_id: str | None = None, *, db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """同一箱号攒到 ≥2 个不同门户ID = 重复上传。返回告警列表。"""
    ensure_schema(db_path)
    conn = sqlite3.connect(str(_db(db_path)))
    conn.row_factory = sqlite3.Row
    try:
        where = "WHERE portal_id IS NOT NULL"
        params: list[Any] = []
        if order_id:
            where += " AND order_id = ?"
            params.append(order_id)
        rows = conn.execute(
            f"""SELECT order_id, box_no, car_no,
                       COUNT(DISTINCT portal_id) n_ids,
                       GROUP_CONCAT(DISTINCT portal_id) portal_ids
                FROM upload_ledger {where}
                GROUP BY order_id, box_no
                HAVING n_ids > 1
                ORDER BY n_ids DESC, box_no""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
