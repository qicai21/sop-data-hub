#!/usr/bin/env python3
"""sop-data-hub CLI 看板(替代 r74_dashboard_server.py / dispatch_board.html)。

特点:
- 终端原生,丢 tmux 后台跑,要看就切过去
- **每 tick 直接 SQL 查 sqlite,无任何缓存**(老 HTML 看板"只显示吉林金钢"
  那个 bug 的根:缓存层 + SQL 漏过滤 — 本脚本彻底绕过)
- 多项目并排:jilin_jingang_jinzhou / chaoyang_steel / zhongtang /
  jiusan / wulanhaote 等,**任何 release_batches 表里有的项目都自动出现**
- 系统状态:5 个 daemon PID + 95306 sync 心跳 + pending 候选数
- 5s 刷新,Ctrl-C 退出
- 时间戳全部走 sop_hub.utils.time(timezone unification,显示 Beijing)
- 0 第三方依赖(stdlib ANSI),不需要装 rich

启动(典型 tmux 用法):
  tmux new -s board
  cd ~/projects/repos/sop-data-hub
  PYTHONPATH=src python3 scripts/cli_dashboard.py
  # Ctrl-B D 后台,Ctrl-B :attach -t board 切回

退出:Ctrl-C
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# 让脚本可以独立跑(自己加 PYTHONPATH)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from sop_hub.utils.time import (
    BEIJING_TZ,
    now_iso_beijing_compact,
    parse_any_timestamp,
)


# ── 配置 ────────────────────────────────────────────────────────────────


DB_PATH = _ROOT / "data" / "sop_agent.db"
RAIL_DB_PATH = Path(
    "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
)
REFRESH_SECONDS = 5

# 项目显示顺序 + 中文名(yaml project_name 字段)
PROJECT_DISPLAY: dict[str, str] = {
    "jilin_jingang_jinzhou": "吉林金钢(锦州)",
    "chaoyang_steel": "朝阳钢铁",
    "zhongtang_special_steel": "中唐特钢",
    "jiusan": "九三循环",
    "wulanhaote_steel": "乌兰浩特",
}

DAEMONS = [
    ("wx-ops-agent", "wechat_ops_agent.cli.main"),
    ("live_service", "run_live_service.py"),
    ("text_watch", "sop_hub.sop.text_watch_daemon"),
    ("rail95306", "run_sync_worker.py"),
]


# ── ANSI 工具 ──────────────────────────────────────────────────────────


def _ansi_clear_home() -> str:
    return "\033[H\033[2J"


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


def _cyan(s: str) -> str:
    return f"\033[36m{s}\033[0m"


def _fixed(s: str, width: int, align: str = "<") -> str:
    """ANSI-aware 定宽截断 / padding(简单版,不处理多色嵌套宽度)。"""
    visible = _strip_ansi(s)
    if len(visible) > width:
        # 截断保留前缀
        kept = visible[: width - 1] + "…"
        return kept
    pad = width - len(visible)
    if align == ">":
        return " " * pad + s
    return s + " " * pad


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


# ── DB 查询(无 cache,每次连)──────────────────────────────────────


def _connect(db_path: Path):
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_projects_with_batches() -> dict[str, list[dict[str, Any]]]:
    """按 project 分组,取所有 in_progress + completed in last 7 days 的 batch。"""
    conn = _connect(DB_PATH)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT
              id, project, ship_name, destination_station, cargo_name,
              batch_sequence, notice_date, batch_date, batch_quantity,
              actual_wagon_count, shipped_weight_tons, remaining_weight_tons,
              dispatch_status, updated_at
            FROM release_batches
            WHERE dispatch_status IN ('in_progress', 'active', 'suspended', 'pending_completion')
               OR (dispatch_status='completed' AND date(updated_at) >= date('now','-7 days'))
            ORDER BY
              CASE dispatch_status
                WHEN 'in_progress' THEN 0
                WHEN 'active' THEN 0
                WHEN 'suspended' THEN 1
                WHEN 'completed' THEN 2
                ELSE 3
              END,
              batch_date DESC
            """
        ).fetchall()
    finally:
        conn.close()

    by_project: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        proj = (r["project"] or "").strip()
        if not proj:
            # 早期脏数据(无 project 字段)— 不显示,避免干扰
            continue
        by_project.setdefault(proj, []).append(dict(r))
    return by_project


def query_pending_candidate_counts() -> dict[str, int]:
    conn = _connect(DB_PATH)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT candidate_status, COUNT(*) AS n
            FROM inspection_ingestion_candidates
            GROUP BY candidate_status
            """
        ).fetchall()
    finally:
        conn.close()
    return {r["candidate_status"] or "(空)": r["n"] for r in rows}


def query_rail95306_freshness() -> dict[str, Any]:
    conn = _connect(RAIL_DB_PATH)
    if conn is None:
        return {"ok": False, "msg": "DB 缺"}
    try:
        row = conn.execute(
            "SELECT MAX(ticketed_at) AS latest, COUNT(*) AS total "
            "FROM shipments WHERE destination_name LIKE '%朝阳%' OR destination_name LIKE '%四平%'"
        ).fetchone()
    finally:
        conn.close()
    latest_dt = parse_any_timestamp(row["latest"]) if row and row["latest"] else None
    return {
        "ok": True,
        "latest": row["latest"] if row else "",
        "latest_beijing": latest_dt.isoformat(timespec="seconds") if latest_dt else "—",
        "total": row["total"] if row else 0,
    }


# ── 进程发现 ──────────────────────────────────────────────────────────


def list_daemons() -> list[dict[str, Any]]:
    """ps aux 找已知 daemon。"""
    import subprocess
    out: list[dict[str, Any]] = []
    try:
        proc = subprocess.run(
            ["ps", "ax", "-o", "pid,etime,command"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception as exc:
        return [{"label": "ps failed", "pid": "?", "etime": "", "ok": False,
                 "msg": str(exc)}]
    lines = proc.stdout.splitlines()
    for label, needle in DAEMONS:
        hit = None
        for line in lines:
            if needle in line and "grep" not in line:
                hit = line.split(None, 2)
                break
        if hit:
            out.append({
                "label": label, "pid": hit[0], "etime": hit[1], "ok": True,
            })
        else:
            out.append({
                "label": label, "pid": "—", "etime": "",
                "ok": False, "msg": "not running",
            })
    return out


# ── 渲染 panel ────────────────────────────────────────────────────────


PANEL_WIDTH = 76


def _box(title: str, lines: list[str], width: int = PANEL_WIDTH) -> list[str]:
    """画框。title 出现在顶边。"""
    top_pre = f"┌─ {_bold(title)} "
    visible_top_pre = len(_strip_ansi(top_pre))
    if visible_top_pre < width - 1:
        top = top_pre + "─" * (width - 1 - visible_top_pre) + "┐"
    else:
        top = top_pre[: width - 1] + "┐"
    bot = "└" + "─" * (width - 2) + "┘"
    body = []
    for ln in lines:
        body.append("│ " + _fixed(ln, width - 4) + " │")
    return [top, *body, bot]


def panel_project(project_id: str, batches: list[dict[str, Any]]) -> list[str]:
    name = PROJECT_DISPLAY.get(project_id, project_id)
    title = f"{name} [{project_id}]  ({len(batches)} 个 batch)"
    if not batches:
        return _box(title, [_dim("  (无 in_progress / 近期 completed batch)")])

    lines: list[str] = []
    # 表头
    lines.append(_dim(
        f"{'船名':<12}{'lot':<7}{'下达日':<11}{'计划t':>8}"
        f"{'已发t':>8}{'剩 t':>8}{'车数':>5}  状态"
    ))
    for b in batches[:8]:  # 最多 8 条/项目
        ship = (b.get("ship_name") or "—")[:11]
        lot = (b.get("batch_sequence") or "—")[:6]
        # 业务上看每个 lot 各自的"下达日期"(batch_date),不是出港单整张的
        # "通知日期"(notice_date)— 一张累计放货单的 notice_date 是共用的,
        # batch_date 才是每段 remark 的下达日。2026-06-04 用户校订。
        notice = (b.get("batch_date") or "—")[:10]
        planned = _num(b.get("batch_quantity"))
        shipped = _num(b.get("shipped_weight_tons"))
        remain = _num(b.get("remaining_weight_tons"))
        wagons = b.get("actual_wagon_count") or 0
        status = b.get("dispatch_status") or "—"
        status_colored = _color_status(status)
        lines.append(
            f"{ship:<12}{lot:<7}{notice:<11}{planned:>8}"
            f"{shipped:>8}{remain:>8}{wagons:>5}  {status_colored}"
        )
    if len(batches) > 8:
        lines.append(_dim(f"  …还有 {len(batches) - 8} 条未显示"))
    return _box(title, lines)


def _num(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
        if n == int(n):
            return f"{int(n)}"
        return f"{n:.1f}"
    except (ValueError, TypeError):
        return str(v)[:7]


def _color_status(s: str) -> str:
    if s in ("in_progress", "active"):
        return _green(s)
    if s in ("pending_review", "pending_95306_match", "suspended",
             "pending_completion"):
        return _yellow(s)
    if s in ("timeout_manual_review", "cancelled"):
        return _red(s)
    if s == "completed":
        return _dim(s)
    return s


def panel_system() -> list[str]:
    lines = []
    daemons = list_daemons()
    for d in daemons:
        if d["ok"]:
            sym = _green("●")
            lines.append(f"  {sym} {d['label']:<15} pid={d['pid']:<7} 起{d['etime']}")
        else:
            sym = _red("○")
            lines.append(f"  {sym} {d['label']:<15} {_red('NOT RUNNING')}")
    lines.append("")
    rail = query_rail95306_freshness()
    if rail.get("ok"):
        lines.append(
            f"  95306 最新票: {_cyan(rail['latest_beijing'])} "
            f"({_num(rail['total'])} 票)"
        )
    else:
        lines.append(f"  95306: {_red(rail.get('msg', '?'))}")
    cands = query_pending_candidate_counts()
    if cands:
        lines.append("  检装车候选:")
        for st, n in sorted(cands.items()):
            sym = _yellow("→") if "pending" in st else _green("→")
            lines.append(f"    {sym} {st:<28} {n:>4}")
    return _box(f"系统状态  ({now_iso_beijing_compact()})", lines)


# ── 主循环 ────────────────────────────────────────────────────────────


def render_once() -> str:
    out_lines: list[str] = []

    # 头部
    out_lines.append(_bold(
        f"  sop-data-hub 看板  ·  {now_iso_beijing_compact()}  ·  "
        f"刷新 {REFRESH_SECONDS}s  ·  Ctrl-C 退出"
    ))
    out_lines.append("")

    by_project = query_projects_with_batches()
    # 显示顺序:已知的按 PROJECT_DISPLAY 顺序,未知的尾后
    known = [p for p in PROJECT_DISPLAY if p in by_project]
    unknown = [p for p in by_project if p not in PROJECT_DISPLAY]
    # jiusan 即使无数据也显示(用户希望预留)
    if "jiusan" not in known:
        known.append("jiusan")

    for proj in known + unknown:
        batches = by_project.get(proj, [])
        out_lines.extend(panel_project(proj, batches))
        out_lines.append("")

    out_lines.extend(panel_system())
    return "\n".join(out_lines)


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB 缺: {DB_PATH}", file=sys.stderr)
        return 2
    try:
        while True:
            sys.stdout.write(_ansi_clear_home())
            sys.stdout.write(render_once())
            sys.stdout.write("\n")
            sys.stdout.flush()
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
