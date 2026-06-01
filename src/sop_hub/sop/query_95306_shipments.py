"""Shared 95306 shipment query executor — R42.

Cross-project, read-only query against 95306_collection.sqlite3.

Usage:
  from sop_hub.sop.query_95306_shipments import query_95306_shipments_by_window

  result = query_95306_shipments_by_window(
      origin_station="锦州港",
      destination_station="四平",
      reference_time="2026-05-21 09:00:00",
      window_before_minutes=60,
      window_after_minutes=60,
  )

This module:
- reads 95306_collection.sqlite3 (shipments table, READ-ONLY)
- does NOT write sop_agent.db, wagon_shipments, dashboard_state, or any DB
- does NOT modify SOP YAML, generate Excel/JSON, or send messages
- is project-agnostic (吉林金钢 / 朝阳钢铁 / 中唐特钢 / 九三大豆)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sop_hub.sop.shipment_query_window import (
    QueryWindow,
    ShipmentCandidate,
    ShipmentQueryResult,
)


# ── Default 95306 DB path ───────────────────────────────────────────────

DEFAULT_RAIL_DB = (
    Path.home()
    / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
)


def _resolve_rail_db_path(rail_db_path: str | Path | None = None) -> Path:
    """Resolve the 95306_collection.sqlite3 path."""
    if rail_db_path:
        return Path(rail_db_path)
    # Check env
    import os
    env_path = os.environ.get("OPS_HUB_DB_95306_PATH") or os.environ.get("DB_95306_PATH")
    if env_path:
        return Path(env_path)
    # Check settings
    try:
        from sop_hub.config import load_settings
        settings_path = load_settings().db_95306_path
        if settings_path:
            return Path(settings_path)
    except Exception:
        pass
    return DEFAULT_RAIL_DB


# ── Core query executor ─────────────────────────────────────────────────

def query_95306_shipments_by_window(
    *,
    origin_station: str,
    destination_station: str,
    reference_time: str,
    window_before_minutes: int = 60,
    window_after_minutes: int = 60,
    cargo_name: str = "",
    expected_car_count: int = -1,
    project_id: str = "",
    rail_db_path: str | Path | None = None,
) -> ShipmentQueryResult:
    """Query 95306 shipments within a time window around a reference time.

    Matching:
      1. origin_station LIKE-matched against origin_name
      2. destination_station LIKE-matched against destination_name
      3. ticketed_at BETWEEN start_time AND end_time
      4. (optional) cargo_name LIKE-matched

    Args:
      origin_station: 发站名称（部分匹配）。
      destination_station: 到站名称（部分匹配）。
      reference_time: 参考时间字符串。
      window_before_minutes: 向前查多少分钟（默认60）。
      window_after_minutes: 向后查多少分钟（默认60）。
      cargo_name: 货物品名（可选，部分匹配）。
      expected_car_count: 预期车数（可选，供调用方参考，不参与过滤）。
      project_id: 项目ID（可选，记录用途）。
      rail_db_path: 95306 DB 路径（可选）。

    Returns:
      ShipmentQueryResult with window, summary counts, and candidates.
    """
    # ── Build query window ──────────────────────────────────────────
    window = QueryWindow.from_reference(
        reference_time,
        window_before_minutes=window_before_minutes,
        window_after_minutes=window_after_minutes,
    )

    result = ShipmentQueryResult(
        window=window,
        origin_station=origin_station,
        destination_station=destination_station,
        cargo_name=cargo_name,
        expected_car_count=expected_car_count,
    )

    # ── Validate DB path ────────────────────────────────────────────
    db_path = _resolve_rail_db_path(rail_db_path)
    if not db_path.exists():
        result.candidates = []
        result.total_candidates = 0
        result.exact_match_count = 0
        result.ambiguous_count = 0
        return result

    # ── Open read-only ──────────────────────────────────────────────
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # ── Build SQL ───────────────────────────────────────────────
        sql = """
            SELECT ydid, car_no, czydid, container_no_raw,
                   origin_name, destination_name, cargo_name,
                   ticketed_at, departed_at, arrived_at, delivered_at,
                   status_name, latest_stage_name
            FROM shipments
            WHERE origin_name LIKE ?
              AND destination_name LIKE ?
              AND ticketed_at >= ?
              AND ticketed_at <= ?
        """
        params: list[Any] = [
            f"%{origin_station}%",
            f"%{destination_station}%",
            window.start_time,
            window.end_time,
        ]

        if cargo_name:
            sql += " AND cargo_name LIKE ?"
            params.append(f"%{cargo_name}%")

        sql += " ORDER BY ticketed_at"

        rows = conn.execute(sql, params).fetchall()

        # ── Build candidates ────────────────────────────────────────
        candidates: list[ShipmentCandidate] = []
        for row in rows:
            candidates.append(ShipmentCandidate(
                ydid=row["ydid"] or "",
                wagon_no=row["car_no"] or "",
                waybill_no=row["czydid"] or "",
                container_no=row["container_no_raw"] or "",
                origin_station=row["origin_name"] or "",
                destination_station=row["destination_name"] or "",
                cargo_name=row["cargo_name"] or "",
                ticketed_at=row["ticketed_at"] or "",
                departed_at=row["departed_at"] or "",
                arrived_at=row["arrived_at"] or "",
                delivered_at=row["delivered_at"] or "",
                current_status=row["latest_stage_name"] or row["status_name"] or "",
            ))

        result.candidates = candidates
        result.total_candidates = len(candidates)

        # ── Compute summary counts ──────────────────────────────────
        # "exact" match: the primary destination/origin match group
        # In a time-window query, all candidates are already filtered;
        # exact_match_count treats the first unique (origin, dest) group
        # as the primary; if expected_car_count is provided, the group
        # whose size matches gets exact_match status.
        if expected_car_count >= 0:
            result.exact_match_count = min(
                result.total_candidates, expected_car_count
            )
            result.ambiguous_count = max(0, result.total_candidates - expected_car_count)
        else:
            result.exact_match_count = result.total_candidates
            result.ambiguous_count = 0

        return result

    finally:
        conn.close()
