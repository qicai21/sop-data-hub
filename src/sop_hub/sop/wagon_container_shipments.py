"""wagon_container_shipments — 集装箱业务专用 wagon 表(#123,2026-06-07)。

每行 = 1 个 (car_no, box_no, ydid) 三元组,**box 级独立 batch_id**(不再
靠 wagon.batch_id + container_batch_map JSON 双源)。

路由策略:
  - 项目 yaml `project_meta.is_container_business: true` → 用这张表
  - 其他项目(朝阳/中唐汐子等整车散运)→ 仍用 wagon_shipments

收益:
  - excel 拆行直接 SELECT,无 cbm JSON
  - factory_upload per box payload 直接遍历
  - factory_verify 直接 SELECT box_no FROM 表
  - dispatch_plan allocate_wagons 直接 UPDATE box.batch_id
  - dashboard 箱数 = COUNT(*)
  - 防"消费方忘了看 cbm 拆"类 bug
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS wagon_container_shipments (
    id TEXT PRIMARY KEY,                       -- hash(car_no|box_no|ydid)
    car_no TEXT NOT NULL,
    box_no TEXT NOT NULL,                      -- 单 box,per row
    box_position INTEGER,                      -- 1 / 2(车上 OCR 顺序)
    ydid TEXT NOT NULL,                        -- 95306 运单 id
    czydid TEXT,
    waybill_no TEXT,
    batch_id TEXT NOT NULL,                    -- 该 box 独立归属 release_batch

    -- wagon 级 95306 共享字段(per box 复制,SQL 自然查 lot 进度)
    car_model TEXT,
    ticketed_at TEXT,
    departed_at TEXT,
    arrived_at TEXT,
    delivered_at TEXT,
    accepted_at TEXT,
    loaded_at TEXT,
    status_name TEXT,
    latest_stage_key TEXT,
    latest_stage_name TEXT,
    latest_event_time TEXT,
    origin_name TEXT,
    destination_name TEXT,
    transport_mode_code TEXT,
    transport_mode_name TEXT,

    -- box / wagon 级混合
    cargo_name TEXT,
    marked_weight REAL,                        -- 车级标载(沿用 95306;每箱列拿同值)

    -- 项目元
    project_id TEXT,
    ship_name TEXT,
    consignor TEXT,
    consignee TEXT,
    dispatch_status TEXT NOT NULL DEFAULT 'in_progress',

    -- 来源跟踪
    source_message_id TEXT,
    source_group_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(car_no, box_no, ydid)
);

CREATE INDEX IF NOT EXISTS idx_wcs_batch ON wagon_container_shipments(batch_id);
CREATE INDEX IF NOT EXISTS idx_wcs_car_ydid ON wagon_container_shipments(car_no, ydid);
CREATE INDEX IF NOT EXISTS idx_wcs_project_ship ON wagon_container_shipments(project_id, ship_name);
CREATE INDEX IF NOT EXISTS idx_wcs_stage ON wagon_container_shipments(latest_stage_key);
"""


def ensure_schema(db_path: str | Path | None = None) -> None:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def is_container_business_project(project_id: str) -> bool:
    """Read project yaml project_meta.is_container_business."""
    if not project_id:
        return False
    try:
        import yaml as _yaml
        from sop_hub.sop.departure_excel import _find_yaml_for_project
        yp = _find_yaml_for_project(project_id)
        raw = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return bool((raw.get("project_meta") or {}).get("is_container_business"))


def insert_box_rows(
    rows: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
) -> dict[str, int]:
    """Insert box-level rows. INSERT OR REPLACE — 同 (car, box, ydid) 三元组幂等。"""
    if not rows:
        return {"inserted": 0, "skipped": 0}
    ensure_schema(db_path=db_path)
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    inserted = 0
    try:
        for r in rows:
            cols = list(r.keys())
            placeholders = ",".join("?" * len(cols))
            col_names = ",".join(cols)
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO wagon_container_shipments ({col_names}) "
                    f"VALUES ({placeholders})",
                    [r[k] for k in cols],
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    finally:
        conn.close()
    return {"inserted": inserted, "skipped": len(rows) - inserted}


def list_boxes_for_batch(
    batch_id: str, *, db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM wagon_container_shipments WHERE batch_id=? "
            "ORDER BY ticketed_at, car_no, box_position",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_boxes_for_batch(
    batch_id: str, *, db_path: str | Path | None = None,
) -> int:
    db = Path(db_path) if db_path else SOP_DB
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM wagon_container_shipments WHERE batch_id=?",
            (batch_id,),
        ).fetchone()[0]
    finally:
        conn.close()
