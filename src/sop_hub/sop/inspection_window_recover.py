"""检装车 95306 时间窗反推 —— 用通知单上的车号当锚点,反查 95306 制票时间,
建时间窗,把窗内同到站/货名的所有制票车号拉回来,作为"真装车"权威列表。

设计动机(2026-06-03 宝腾海 53→52 漏触发根因):
  VLM 抽通知单时可能把真装车误标成排车(无理由 defect),导致按通知单标签
  统计出 52 而实际 53。**95306 的制票数才是权威**:同一批检装车通常在密集
  时间窗内统一制票,只要有 1 个车号能在 95306 上找到 ticketed_at,就能反推
  出整个窗内的真实制票车号集合。

业务铁律:
  - 朝阳西不允许拼列(用户口述)→ 同一时间窗内同到站+货名的票都属于同一批。
  - 先按通知单"非排车"顺序找锚点;前 N 个都查不到 → "还没制单",挂起等。
  - 窗内 ∩ 通知单全部车号 = 真装车;通知单独有 = 真排车;窗内独有 = 拼批异常。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def recover_loading_cars_via_window(
    *,
    rail_db_path: str | Path,
    loading_car_nos: list[str],
    all_notice_car_nos: list[str],
    destination: str,
    cargo_pattern: str = "%铁矿%",
    max_anchor_attempts: int = 4,
    window_minutes: int = 120,
) -> dict[str, Any]:
    """用通知单车号反推 95306 时间窗,返回窗内权威装车列表。

    Args:
      rail_db_path: 95306_collection.sqlite3 路径(只读)。
      loading_car_nos: 通知单上**非排车**(VLM 标的装车)的 car_no,**保持通知单
        原始顺序** —— 用于在 95306 上找第一个有票的当锚点。
      all_notice_car_nos: 通知单上**全部**车号(装+排) —— 用于跟窗内集合求交。
      destination: 到站(e.g. "朝阳西")。
      cargo_pattern: 货名 LIKE 模式(默认 "%铁矿%")。
      max_anchor_attempts: 找锚点最多尝试前几个 loading 车号(默认 4,业务约定)。
      window_minutes: 锚点 ticketed_at 前后各几分钟为窗(默认 120,即 ±2h)。

    Returns:
      {
        "status": "ok"            ← 找到锚点 + 窗内/通知单交集干净,可放心用
                 | "no_ticket_yet"← 前 max_anchor_attempts 个 loading 都没票,挂起等
                 | "anomaly"      ← 找到锚点但有异常(如拼批),需人工裁决
                 ,
        "loading_car_nos": [...],  ← 窗内 ∩ 通知单全部车号 = 真装车权威列表
        "anchor_car_no": "..." | None,
        "anchor_ticketed_at": "..." | None,
        "window_minutes": int,
        "window_total_count": int,  ← 95306 窗内总数(去重)
        "missing_from_notice": [...],  ← 窗内有但通知单没的(拼批/窗太宽)
        "notice_only": [...],          ← 通知单有但 95306 没的(真排车)
        "anchor_attempts": int,
        "message": "...",
      }
    """
    if not loading_car_nos:
        return {
            "status": "no_ticket_yet",
            "loading_car_nos": [],
            "anchor_car_no": None,
            "anchor_ticketed_at": None,
            "window_minutes": window_minutes,
            "window_total_count": 0,
            "missing_from_notice": [],
            "notice_only": [],
            "anchor_attempts": 0,
            "message": "通知单装车列表为空,无锚点可用",
        }

    rail = sqlite3.connect(f"file:{rail_db_path}?mode=ro", uri=True)
    rail.row_factory = sqlite3.Row
    try:
        # ── 1. 走通知单装车顺序,找第一个 95306 已制票的当锚点 ─────────
        anchor_car: str | None = None
        anchor_ts: str | None = None
        attempts = 0
        for c in loading_car_nos:
            c = (c or "").strip()
            if not c:
                continue
            attempts += 1
            r = rail.execute(
                "SELECT ticketed_at FROM shipments "
                "WHERE car_no=? AND destination_name=? AND cargo_name LIKE ? "
                "  AND ticketed_at IS NOT NULL AND ticketed_at != '' "
                "ORDER BY ticketed_at DESC LIMIT 1",
                (c, destination, cargo_pattern),
            ).fetchone()
            if r and r["ticketed_at"]:
                anchor_car = c
                anchor_ts = r["ticketed_at"]
                break
            if attempts >= max_anchor_attempts:
                break

        if anchor_car is None:
            return {
                "status": "no_ticket_yet",
                "loading_car_nos": [],
                "anchor_car_no": None,
                "anchor_ticketed_at": None,
                "window_minutes": window_minutes,
                "window_total_count": 0,
                "missing_from_notice": [],
                "notice_only": [],
                "anchor_attempts": attempts,
                "message": (
                    f"通知单前 {attempts} 个装车号在 95306 都没制票 → "
                    f"还没制单,挂起等 95306 同步"
                ),
            }

        # ── 2. 用锚点 ticketed_at 建窗,查窗内所有 dest+cargo 制票车号 ──
        rows = rail.execute(
            "SELECT DISTINCT car_no FROM shipments "
            "WHERE destination_name=? AND cargo_name LIKE ? "
            "  AND ticketed_at IS NOT NULL AND ticketed_at != '' "
            "  AND ticketed_at BETWEEN datetime(?, ?) AND datetime(?, ?)",
            (
                destination, cargo_pattern,
                anchor_ts, f"-{window_minutes} minutes",
                anchor_ts, f"+{window_minutes} minutes",
            ),
        ).fetchall()
        window_cars = {r["car_no"] for r in rows if r["car_no"]}
        notice_set = {c for c in all_notice_car_nos if c}

        # ── 3. 集合运算:权威装车 = 窗内 ∩ 通知单 ─────────────────────
        loading = sorted(window_cars & notice_set)
        missing_from_notice = sorted(window_cars - notice_set)
        notice_only = sorted(notice_set - window_cars)

        # ── 4. 异常 sanity ──────────────────────────────────────────────
        status = "ok"
        msg = (
            f"锚点车 {anchor_car} 制票 {anchor_ts};窗内 ±{window_minutes} 分钟"
            f" {len(window_cars)} 车;交集(真装车) {len(loading)};"
            f"通知单独有(真排车) {len(notice_only)}"
        )
        if missing_from_notice:
            # 窗内有车号但通知单没列 → 可能是拼批、窗太宽、或别的列
            # 朝阳西按业务铁律不许拼列 → 这种就是异常,挂人工裁决
            status = "anomaly"
            msg += f";异常:95306 窗内 {len(missing_from_notice)} 车不在通知单上"

        return {
            "status": status,
            "loading_car_nos": loading,
            "anchor_car_no": anchor_car,
            "anchor_ticketed_at": anchor_ts,
            "window_minutes": window_minutes,
            "window_total_count": len(window_cars),
            "missing_from_notice": missing_from_notice,
            "notice_only": notice_only,
            "anchor_attempts": attempts,
            "message": msg,
        }
    finally:
        rail.close()
