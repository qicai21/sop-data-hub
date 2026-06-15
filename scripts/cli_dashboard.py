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
from functools import lru_cache
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


# ── 显示宽度 helpers(中文 / 全角 = 2 cell,ASCII = 1 cell)─────────────
def _disp_width(s: str) -> int:
    import unicodedata
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _truncate_disp(s: str, max_w: int) -> str:
    import unicodedata
    out: list[str] = []
    cur = 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur + cw > max_w:
            break
        out.append(ch)
        cur += cw
    return "".join(out)


def _pad_disp(s: str, width: int, align: str = "left") -> str:
    """按终端 cell 宽度 pad — 中文 2 cell,ASCII 1 cell。
    ANSI 颜色码会算进 _disp_width 是 0(不可见),但调用方要在 pad 之后再套色码。
    """
    cur = _disp_width(s)
    if cur >= width:
        return s
    pad = " " * (width - cur)
    return s + pad if align == "left" else pad + s


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
    """ANSI-aware + East Asian Width-aware 定宽。中文 2 cell ASCII 1 cell。"""
    visible = _strip_ansi(s)
    cur = _disp_width(visible)
    if cur > width:
        # 按 cell 截断(_truncate_disp 处理中文宽),颜色码丢了认了
        return _truncate_disp(visible, width - 1) + "…"
    pad = width - cur
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


_last_sw_recompute = 0.0


def refresh_active_shipped_weights(throttle_s: int = 30) -> None:
    """看板刷新前,对活跃 batch 重算装车重量,保证任何入库路径(含手工补车、
    backfill 脚本)后看板都显示准确值 —— 否则补车没重算就会偏小、失真,
    影响请车统计(2026-06-15 宝腾海 14942.5 漏 15 车实证)。

    write_basis=False → 只刷 batch 级合计,廉价。无 yaml 规则的项目(如 jiusan)
    compute 直接返回不写,保留 sync 脚本落的值,互不干扰。节流最多每 throttle_s 秒
    一次;任何异常静默,看板绝不因此崩。
    """
    global _last_sw_recompute
    now = time.time()
    if now - _last_sw_recompute < throttle_s:
        return
    _last_sw_recompute = now
    conn = _connect(DB_PATH)
    if conn is None:
        return
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM release_batches "
            "WHERE dispatch_status NOT IN ('confirmed_received','closed')"
        ).fetchall()]
    except sqlite3.Error:
        return
    finally:
        conn.close()
    try:
        from sop_hub.sop.shipped_weight import compute_for_release_batch
    except Exception:
        return
    for bid in ids:
        try:
            compute_for_release_batch(bid, db_path=DB_PATH, write_basis=False)
        except Exception:
            continue


def query_projects_with_batches() -> dict[str, list[dict[str, Any]]]:
    """按 project 分组,取所有 in_progress + completed in last 7 days 的 batch。
    顺手补一列 box_count(集装箱业务用,从 wagon_shipments.container_numbers_json
    聚合;散运业务忽略此列,继续看 actual_wagon_count)。
    """
    conn = _connect(DB_PATH)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT
              rb.id, rb.project, rb.ship_name, rb.destination_station, rb.cargo_name,
              rb.batch_sequence, rb.notice_date, rb.batch_date, rb.batch_quantity,
              rb.actual_wagon_count, rb.shipped_weight_tons, rb.remaining_weight_tons,
              rb.dispatch_status, rb.updated_at,
              -- #123 Phase 2:先看新表 wagon_container_shipments(集装箱业务),
              -- 它已迁完,直接 COUNT(*) 就是 box 数;新表没数据(整车业务/历史)
              -- 退回老 wagon_shipments 拆 cbm 路径。
              CASE WHEN (SELECT COUNT(*) FROM wagon_container_shipments wcs
                         WHERE wcs.batch_id=rb.id) > 0
                THEN (SELECT COUNT(*) FROM wagon_container_shipments wcs
                      WHERE wcs.batch_id=rb.id)
                ELSE COALESCE((
                  SELECT SUM(json_array_length(
                    CASE WHEN ws.container_numbers_json IS NULL
                              OR ws.container_numbers_json=''
                         THEN '[]'
                         ELSE ws.container_numbers_json END))
                  FROM wagon_shipments ws
                  WHERE ws.batch_id=rb.id AND ws.container_batch_map IS NULL
                ), 0) + COALESCE((
                  SELECT COUNT(*)
                  FROM wagon_shipments ws, json_each(ws.container_batch_map) j
                  WHERE ws.container_batch_map IS NOT NULL AND j.value=rb.id
                ), 0)
              END AS box_count
            FROM release_batches rb
            -- #125 lifecycle 新枚举:dashboard 只显示活跃区(已结算 confirmed_received/closed
            -- 不再展示,看板只关注在跑的 lot)
            WHERE rb.dispatch_status IN ('pending_freight','enriched','loading','all_loaded','tracking','delivered')
            ORDER BY
              CASE rb.dispatch_status
                WHEN 'loading' THEN 0
                WHEN 'enriched' THEN 1
                WHEN 'all_loaded' THEN 2
                WHEN 'tracking' THEN 3
                WHEN 'delivered' THEN 4
                WHEN 'pending_freight' THEN 5
                WHEN 'confirmed_received' THEN 6
                WHEN 'closed' THEN 7
                ELSE 9
              END,
              rb.batch_date DESC
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


@lru_cache(maxsize=1)
def _container_business_projects() -> set[str]:
    """读所有 yaml 找 project_meta.is_container_business=True 的 canonical id 集。
    用于 dashboard 选用"箱数"还是"车数"列。
    """
    import yaml as _yaml
    from pathlib import Path as _P
    result: set[str] = set()
    sop_dir = _P(__file__).resolve().parents[1] / "config" / "project_sops"
    if not sop_dir.exists():
        return result
    for yp in sop_dir.glob("*.yaml"):
        try:
            data = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        pid = (data.get("project_id") or "").strip()
        meta = data.get("project_meta") or {}
        if pid and bool(meta.get("is_container_business")):
            result.add(pid)
    return result


@lru_cache(maxsize=1)
def _project_lifecycle_modes() -> dict[str, str]:
    """canonical project_id → lifecycle.mode("shipped_is_completed" / "full_track_to_received")
    yaml 没配的默认 "full_track_to_received"(保守 — 跟到底)。
    """
    import yaml as _yaml
    from pathlib import Path as _P
    result: dict[str, str] = {}
    sop_dir = _P(__file__).resolve().parents[1] / "config" / "project_sops"
    if not sop_dir.exists():
        return result
    for yp in sop_dir.glob("*.yaml"):
        try:
            data = _yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        pid = (data.get("project_id") or "").strip()
        if not pid:
            continue
        meta = data.get("project_meta") or {}
        mode = ((meta.get("lifecycle") or {}).get("mode") or "").strip()
        if mode not in ("shipped_is_completed", "full_track_to_received"):
            mode = "full_track_to_received"
        result[pid] = mode
    return result


def _life_tag(project: str) -> str:
    """1 字标签:S=shipped(短链发运即完);F=full(全程跟到货)。"""
    mode = _project_lifecycle_modes().get(project, "full_track_to_received")
    return "S" if mode == "shipped_is_completed" else "F"


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


PANEL_WIDTH = 92


def _box(title: str, lines: list[str], width: int = PANEL_WIDTH) -> list[str]:
    """画框。title 出现在顶边。中文 title 也按 cell 算宽。"""
    top_pre = f"┌─ {_bold(title)} "
    visible_top_pre = _disp_width(_strip_ansi(top_pre))
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
    # 标题加 lifecycle 标识:[S]=shipped_is_completed 短链 / [F]=full_track_to_received 全程
    life_tag = _life_tag(project_id)
    title = f"{name} [{project_id}]  [{life_tag}]  ({len(batches)} 个 batch)"
    if not batches:
        return _box(title, [_dim("  (无 in_progress / 近期 completed batch)")])

    lines: list[str] = []
    # 集装箱项目(yaml is_container_business=true)显示"箱数",散运显示"车数"
    is_container = project_id in _container_business_projects()
    unit_label = "箱数" if is_container else "车数"

    # 表头(用 _pad_disp 按终端 cell 宽度对齐 — 中文 2 cell)
    header = (
        _pad_disp("船名", 14)
        + _pad_disp("lot", 7)
        + _pad_disp("下达日", 12)
        + _pad_disp("计划t", 8, "right")
        + _pad_disp("已发t", 8, "right")
        + _pad_disp("剩 t", 8, "right")
        + _pad_disp(unit_label, 5, "right")
        + "  状态"
    )
    lines.append(_dim(header))
    for b in batches[:8]:  # 最多 8 条/项目
        ship = _truncate_disp(b.get("ship_name") or "—", 13)
        lot = _truncate_disp(b.get("batch_sequence") or "—", 6)
        # 业务上看每个 lot 各自的"下达日期"(batch_date),不是出港单整张的
        # "通知日期"(notice_date)— 一张累计放货单的 notice_date 是共用的,
        # batch_date 才是每段 remark 的下达日。2026-06-04 用户校订。
        notice = _truncate_disp(b.get("batch_date") or "—", 11)
        planned = _num(b.get("batch_quantity"))
        shipped = _num(b.get("shipped_weight_tons"))
        # 剩余防御:存值优先,NULL 时现算 计划-已发(防 batch 没及时 compute
        # shipped_weight 就显 "—";已发0 时剩余应=计划,而非空)
        remain_v = b.get("remaining_weight_tons")
        if remain_v is None and b.get("batch_quantity") is not None:
            try:
                remain_v = float(b.get("batch_quantity")) - float(b.get("shipped_weight_tons") or 0)
            except (ValueError, TypeError):
                remain_v = None
        remain = _num(remain_v)
        unit_count = str(
            (b.get("box_count") if is_container else b.get("actual_wagon_count"))
            or 0
        )
        status = b.get("dispatch_status") or "—"
        # status 列固定 19 cell — 容纳 pending_completion 全名,所有行右
        # 边框自然对齐(_color_status 改成 strip 后判颜色,pad 不影响)
        status_colored = _color_status(_pad_disp(status, 19))
        lines.append(
            _pad_disp(ship, 14)
            + _pad_disp(lot, 7)
            + _pad_disp(notice, 12)
            + _pad_disp(planned, 8, "right")
            + _pad_disp(shipped, 8, "right")
            + _pad_disp(remain, 8, "right")
            + _pad_disp(unit_count, 5, "right")
            + "  " + status_colored
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
    # 调用方可传 pad 过的字符串(含末尾空格);判颜色用 strip,套色码保留原串
    # #125: lifecycle 8 值新枚举着色
    key = s.strip()
    if key in ("loading", "enriched"):                          # 进行中
        return _green(s)
    if key in ("all_loaded", "tracking", "delivered"):          # 路上 / 等签收
        return _yellow(s)
    if key in ("pending_freight", "pending_review",
               "pending_95306_match"):                          # 等用户/数据
        return _yellow(s)
    if key in ("timeout_manual_review", "cancelled"):
        return _red(s)
    if key in ("confirmed_received", "closed"):                 # 已完成
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
        # 横排打包:挂起态优先(黄)、终态其后(灰),按盒宽折行,省竖向空间
        OPEN = {"candidate", "pending_match", "pending_review",
                "pending_95306_match", "timeout_manual_review"}
        open_n = sum(n for st, n in cands.items() if st in OPEN)
        head = "  检装车候选(挂起 " + (_yellow(str(open_n)) if open_n else _green("0")) + "):  "
        # 排序:挂起态在前,然后按数量降序
        ordered = sorted(cands.items(), key=lambda kv: (kv[0] not in OPEN, -kv[1], kv[0]))
        items = [
            (f"{_yellow(st)} {_yellow(str(n))}" if st in OPEN else f"{_dim(st)} {n}")
            for st, n in ordered
        ]
        content_w = PANEL_WIDTH - 4
        indent = " " * 16  # 续行缩进,跟首行内容对齐
        sep, sep_w = "  ·  ", 5
        cur = head
        cur_w = _disp_width(_strip_ansi(cur))
        for i, it in enumerate(items):
            it_w = _disp_width(_strip_ansi(it))
            add_w = it_w if i == 0 else sep_w + it_w
            if cur_w + add_w > content_w and cur.strip():
                lines.append(cur)
                cur, cur_w = indent + it, _disp_width(indent) + it_w
            else:
                cur += (it if cur.endswith("  ") else sep + it)
                cur_w += add_w
        lines.append(cur)
    return _box(f"系统状态  ({now_iso_beijing_compact()})", lines)


# ── 主循环 ────────────────────────────────────────────────────────────


def panel_paths() -> list[str]:
    """常用 DB / 路径提示 — 跟 Claude 对话时直接引用,不用临时回忆。"""
    rows = [
        f"  {_dim('业务库')}     data/sop_agent.db",
        f"  {_dim('95306 库')}   ~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3",
        f"  {_dim('微信流水')}   ~/projects/repos/wx-ops-agent/data/chat_records/<群>/<YYYY-MM>.jsonl",
        f"  {_dim('归档根')}     ~/Documents/bussiness-artifacts/wechat_images/business/projects/<canonical_id>/",
        f"  {_dim('yaml SOP')}   config/project_sops/<canonical_id>.yaml",
    ]
    return _box("常用数据库 / 路径", rows)


def render_once() -> str:
    out_lines: list[str] = []

    # 刷新前先把活跃 batch 的装车重量重算准(任何入库路径都兜底)
    refresh_active_shipped_weights()

    # 头部
    out_lines.append(_bold(
        f"  sop-data-hub 看板  ·  {now_iso_beijing_compact()}  ·  "
        f"刷新 {REFRESH_SECONDS}s  ·  Ctrl-C 退出"
    ))
    out_lines.append("")

    out_lines.extend(panel_paths())
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
