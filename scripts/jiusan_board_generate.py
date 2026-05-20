#!/usr/bin/env python3
"""
九三大豆循环运输看板生成器（V3 架构）

读取 jiusan_cycle.db + 95306 实时状态 → 生成 dashboard JSON + HTML

V3 核心设计原则：
- 运行状态以 95306 最新事件为准，单向映射，不搞"预期 vs 实际"冲突检测
- 循环列状态 = 95306 latest_event 的如实反映
- 三账校验：Snapshot + Flow + Adjustment 独立存证，交叉验证

Usage:
    python3 scripts/jiusan_board_generate.py
    python3 scripts/jiusan_board_generate.py --example  # 同时生成 example JSON
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
JIUSAN_DB = REPO_ROOT / "data" / "jiusan_cycle.db"
AGENT_DB = REPO_ROOT / "data" / "agent.db"
DASHBOARD_DIR = REPO_ROOT / "dashboard"
RAIL95306_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")

STATUS_LABELS = {
    "30": "已装车",
    "35": "已制单",
    "40": "已发车（在途）",
    "60": "已到达",
    "80": "货物已交付",
}


def get_conn():
    conn = sqlite3.connect(str(JIUSAN_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _load_tracking_summary(conn, train_id: str) -> dict:
    """从 jiusan_tracking_status 表加载某列的追踪摘要"""
    rows = conn.execute(
        """SELECT 
               COUNT(*) as total,
               SUM(CASE WHEN status_code = '40' THEN 1 ELSE 0 END) as departed,
               SUM(CASE WHEN status_code = '60' THEN 1 ELSE 0 END) as arrived,
               SUM(CASE WHEN status_code = '80' THEN 1 ELSE 0 END) as delivered,
               MIN(latest_event_time) as earliest_event,
               MAX(latest_event_time) as latest_event
           FROM jiusan_tracking_status
           WHERE train_id = ?""",
        (train_id,),
    ).fetchone()

    if not rows or rows["total"] == 0:
        return {}

    latest = conn.execute(
        "SELECT car_no, latest_event, latest_event_time, status_name, status_code, current_node "
        "FROM jiusan_tracking_status "
        "WHERE train_id = ? ORDER BY latest_event_time DESC LIMIT 1",
        (train_id,),
    ).fetchone()

    departed_row = conn.execute(
        "SELECT departed_at FROM jiusan_tracking_status "
        "WHERE train_id = ? AND departed_at IS NOT NULL LIMIT 1",
        (train_id,),
    ).fetchone()

    result = dict(rows)
    if latest:
        result["summary_status"] = STATUS_LABELS.get(latest["status_code"], latest["status_name"] or "未知")
        result["latest_event"] = latest["latest_event"]
        result["latest_event_time"] = latest["latest_event_time"]
        result["current_location"] = latest["current_node"] or "未知"
    if departed_row:
        result["depart_at"] = departed_row["departed_at"]
    return result


def _load_resource_pool(conn) -> dict:
    """从 jiusan_resource_pool 表加载最新资源池快照"""
    container = conn.execute(
        "SELECT * FROM jiusan_resource_pool WHERE pool_type = 'container' ORDER BY last_updated DESC LIMIT 1"
    ).fetchone()
    wagon = conn.execute(
        "SELECT * FROM jiusan_resource_pool WHERE pool_type = 'wagon' ORDER BY last_updated DESC LIMIT 1"
    ).fetchone()

    block = {}
    if container:
        block["container"] = dict(container)
    if wagon:
        block["wagon"] = dict(wagon)
    return block


def _load_warnings(conn) -> list:
    """从 jiusan_warnings 表加载未解决的预警"""
    rows = conn.execute(
        "SELECT * FROM jiusan_warnings WHERE acknowledged = 0 ORDER BY warning_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _load_shipment_plan_days(conn, plan_id: str) -> list:
    """加载发运计划按日明细"""
    rows = conn.execute(
        "SELECT * FROM jiusan_shipment_plan_days WHERE plan_id = ? ORDER BY seq_no",
        (plan_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_95306_daily_stats() -> dict:
    """从 95306 数据库读取今天的大豆发运统计（只读）"""
    if not RAIL95306_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(RAIL95306_DB))
        conn.row_factory = sqlite3.Row

        # 今天高桥镇→新台子大豆集装箱
        container_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM shipments "
            "WHERE cargo_name = '大豆' AND origin_name = '高桥镇' "
            "AND destination_name = '新台子' "
            "AND ticketed_at >= date('now') "
            "AND transport_mode_name = '集装箱运输'"
        ).fetchone()

        # 已发车（在途）的数量
        on_way = conn.execute(
            "SELECT COUNT(*) as cnt FROM shipments "
            "WHERE cargo_name = '大豆' AND origin_name = '高桥镇' "
            "AND destination_name = '新台子' "
            "AND ticketed_at >= date('now', '-7 days') "
            "AND status_code = '40'"
        ).fetchone()

        conn.close()
        return {
            "today_container_count": container_today["cnt"] if container_today else 0,
            "on_way_count_7day": on_way["cnt"] if on_way else 0,
            "source": "rail95306-sync (只读)",
        }
    except Exception:
        return {}


def generate(conn) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now().strftime("%Y-%m-%d")

    # ── 读取各表数据 ──

    # 循环列
    trains = conn.execute(
        "SELECT * FROM jiusan_cycle_trains ORDER BY id"
    ).fetchall()

    # 运行记录
    runs = conn.execute(
        "SELECT * FROM jiusan_cycle_train_runs ORDER BY train_id, round_no"
    ).fetchall()
    runs_by_train = {}
    for r in runs:
        runs_by_train.setdefault(r["train_id"], []).append(dict(r))

    # 资源事件
    events = conn.execute(
        "SELECT * FROM jiusan_resource_events ORDER BY created_at DESC"
    ).fetchall()

    # 流量
    flows = conn.execute(
        "SELECT * FROM jiusan_flows ORDER BY flow_date DESC"
    ).fetchall()

    # 快照
    snapshots = conn.execute(
        "SELECT * FROM jiusan_snapshots ORDER BY snapshot_date DESC"
    ).fetchall()

    # 调整
    adjustments = conn.execute(
        "SELECT * FROM jiusan_adjustments ORDER BY adj_date DESC"
    ).fetchall()

    # 计划
    active_plan = conn.execute(
        "SELECT * FROM jiusan_shipment_plans WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    # 库存
    inv = conn.execute(
        "SELECT * FROM jiusan_factory_inventory ORDER BY record_date DESC LIMIT 1"
    ).fetchone()

    # 扫描日志
    last_scan = conn.execute(
        "SELECT * FROM jiusan_95306_scan_log ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    # ── V3 新增数据源 ──

    # 资源池
    resource_pool = _load_resource_pool(conn)

    # 预警
    warnings = _load_warnings(conn)

    # 95306 实时统计（只读）
    daily_95306 = _load_95306_daily_stats()

    # ── 构建循环列区块 ──
    cycle_trains_block = []
    for t_row in trains:
        t = dict(t_row)
        t_runs = runs_by_train.get(t["id"], [])
        last_run = t_runs[-1] if t_runs else None

        # 从 tracking_status 获取实时状态
        ts = _load_tracking_summary(conn, t["id"])

        train_entry = {
            "train_id": t["id"],
            "type": "集装箱" if t["train_type"] == "container" else "散粮",
            "lot": t["lot"],
            "status": t["status"],
            "status_text": ts.get("summary_status", "待同步"),
            "current_round": t["current_round"],
            "destination_line": t["destination_line"],
            "departure_window": t.get("departure_window"),
            "trains_per_day": t.get("trains_per_day", 1),
            "target_wagon_count": t.get("target_wagon_count"),
            "return_to_port": t.get("return_to_port", 1),
            # 95306 实时追踪
            "tracking": {
                "total_cars": ts.get("total", 0),
                "departed_cars": ts.get("departed", 0),
                "arrived_cars": ts.get("arrived", 0),
                "delivered_cars": ts.get("delivered", 0),
                "latest_event": ts.get("latest_event", ""),
                "latest_event_time": ts.get("latest_event_time", ""),
                "current_location": ts.get("current_location", ""),
                "depart_at": ts.get("depart_at", ""),
                "summary_status": ts.get("summary_status", "待同步"),
            },
        }
        if last_run:
            train_entry["last_run"] = {
                "round_no": last_run["round_no"],
                "wagon_count": last_run["wagon_count"],
                "container_count": last_run["container_count"],
                "total_weight": last_run["total_weight"],
                "depart_time": last_run["depart_time"],
                "arrive_time": last_run["arrive_time"],
                "unload_time": last_run["unload_time"],
                "return_time": last_run["return_time"],
                "status": last_run["status"],
                "notes": last_run["notes"],
            }
        cycle_trains_block.append(train_entry)

    # ── 构建散粮一次性发运区块 ──
    one_time_shipments = []
    for e in events:
        if e["event_type"] == "bulk_dispatch_once":
            entry = dict(e)
            one_time_shipments.append(entry)

    # ── 构建三账校验区块 ──
    three_accounts_block = {
        "snapshots": [dict(s) for s in snapshots],
        "flows": [dict(f) for f in flows],
        "adjustments": [dict(a) for a in adjustments],
        "verification": {
            "status": "pending",
            "note": "完整三账校验请运行 scripts/jiusan_three_account_verify.py。看板仅展示原始数据。",
        },
    }

    # ── 构建 factory_inventory 区块 ──
    inv_block = None
    if inv:
        inv_dict = dict(inv)
        inv_notes = inv_dict.get("notes", "") or ""
        data_quality = (
            "sample"
            if "data_quality=sample" in inv_notes
            else (
                "user_reported"
                if "data_quality=user_reported" in inv_notes
                else "actual" if "data_quality=actual" in inv_notes else "unknown"
            )
        )
        inv_block = {
            "record_date": inv_dict["record_date"],
            "opening_stock": inv_dict["opening_stock"],
            "line_in_qty": inv_dict["line_in_qty"],
            "other_source_in_qty": inv_dict["other_source_in_qty"],
            "consumption": inv_dict["consumption"],
            "closing_stock": inv_dict["closing_stock"],
            "adjustment": inv_dict["adjustment"],
            "red_line": inv_dict["red_line"],
            "days_supported": round(inv_dict["days_supported"], 1) if inv_dict["days_supported"] else None,
            "data_quality": data_quality,
            "below_red_line": (inv_dict["closing_stock"] or 0) < (inv_dict["red_line"] or 0),
            "other_source_inferred": round(
                inv_dict["closing_stock"]
                - inv_dict["opening_stock"]
                - inv_dict["line_in_qty"]
                + inv_dict["consumption"]
                - (inv_dict["adjustment"] or 0),
                2,
            ),
        }

    # ── 构建 active_plan 区块 ──
    plan_block = None
    plan_days_block = []
    if active_plan:
        plan_block = dict(active_plan)
        if "plan_details_json" in plan_block:
            try:
                plan_block["details"] = json.loads(plan_block["plan_details_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        plan_days_block = _load_shipment_plan_days(conn, active_plan["id"])

    # ── 构建 daily_summary 区块 ──
    container_cars_on_way = sum(
        t.get("tracking", {}).get("departed_cars", 0) - t.get("tracking", {}).get("arrived_cars", 0)
        for t in cycle_trains_block
        if t["type"] == "集装箱"
    )
    total_container_dispatched = sum(
        t.get("tracking", {}).get("departed_cars", 0)
        for t in cycle_trains_block
        if t["type"] == "集装箱"
    )

    daily_summary = {
        "date": date_str,
        "today_dispatched_wagons": daily_95306.get("today_container_count", 0),
        "container_on_way": container_cars_on_way,
        "container_total_dispatched": total_container_dispatched,
        "bulk_dispatch_once": [s["quantity"] for s in one_time_shipments] if one_time_shipments else [0],
        "source": "95306 实时",
        "note": "已发车数据直接来源于 95306 shipments 表，无预期冲突检测",
    }

    # ── 构建 pending_confirmations ──
    pending = [
        {
            "type": "train_reorganization",
            "question": "集装箱两列后续是否重组/合并？",
            "confirmed": False,
        },
        {
            "type": "train_01_return",
            "question": "container_train_01 是否已返回锦州港？当前标记为 returned，需人工确认实际到港时间。",
            "confirmed": False,
        },
        {
            "type": "pool_quantity",
            "question": "集装箱池当前约 212 只、车体池约 106 辆为估算值，是否需要调整基准配置量？",
            "confirmed": False,
        },
    ]
    # 库存预警自动追加
    if inv_block and inv_block.get("below_red_line"):
        pending.append({
            "type": "inventory_redline",
            "question": f"库存 {inv_block['closing_stock']} 吨已跌破红线 {inv_block['red_line']} 吨，是否补充发运或确认其他来源入库？",
            "confirmed": False,
        })

    # ── 组装 dashboard ──
    dashboard = {
        "_meta": {
            "dashboard_name": "九三大豆循环运输看板",
            "version": "3.0",
            "generated_at": now,
            "data_sources": ["jiusan_cycle.db", "rail95306-sync (只读)"],
            "architecture": "V3 — 循环运输资源账 + 运行态势 + 运力计划 + 库存风险预警",
            "design_principles": [
                "运行状态以 95306 最新事件为准单向映射",
                "不搞预期 vs 实际冲突检测",
                "三账独立存证交叉验证",
            ],
        },
        "summary": daily_summary,
        "current_plan": {
            "plan": plan_block,
            "plan_days": plan_days_block,
        },
        "resource_pool": resource_pool,
        "cycle_trains": cycle_trains_block,
        "one_time_shipments": one_time_shipments,
        "three_accounts": three_accounts_block,
        "factory_inventory": inv_block,
        "warnings": warnings,
        "pending_confirmations": pending,
        "95306_status": {
            "last_scan": last_scan["scan_time"] if last_scan else None,
            "new_records": last_scan["new_records"] if last_scan else None,
            "daily_stats": daily_95306,
        },
        "refresh_policy": {
            "mode": "manual",
            "auto_refresh_enabled": False,
            "last_generated_at": now,
            "note": "Phase 1 阶段无定时任务。执行 python3 scripts/jiusan_refresh_board.py 后刷新浏览器。",
        },
    }

    return dashboard


def generate_html(dashboard: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = dashboard.get("_meta", {})
    summary = dashboard.get("summary", {})
    resource_pool = dashboard.get("resource_pool", {})
    cycle_trains = dashboard.get("cycle_trains", [])
    one_time = dashboard.get("one_time_shipments", [])
    three_acc = dashboard.get("three_accounts", {})
    inv = dashboard.get("factory_inventory", {}) or {}
    warnings = dashboard.get("warnings", [])
    pending = dashboard.get("pending_confirmations", [])
    plan_info = dashboard.get("current_plan", {}).get("plan", {}) or {}
    plan_days = dashboard.get("current_plan", {}).get("plan_days", []) or []

    # ── 今日摘要 ──
    dispatched = summary.get("today_dispatched_wagons", 0)
    on_way = summary.get("container_on_way", 0)
    soon_arrive = max(0, on_way - sum(t.get("tracking", {}).get("arrived_cars", 0) for t in cycle_trains))

    # ── 循环列卡片 ──
    trains_html = ""
    for t in cycle_trains:
        tr = t.get("tracking", {})
        lr = t.get("last_run", {})
        status = t.get("status_text", "待同步")
        latest_time = tr.get("latest_event_time", "")
        location = tr.get("current_location", "")
        total = tr.get("total_cars", 0)
        departed = tr.get("departed_cars", 0)
        arrived = tr.get("arrived_cars", 0)
        depart_at = tr.get("depart_at", "")
        if depart_at and len(depart_at) > 16:
            depart_at = depart_at[:16]
        if latest_time and len(latest_time) > 16:
            latest_time = latest_time[:16]

        # 状态颜色
        status_color = {
            "returned": "status-returned",
            "unloaded": "status-unloaded",
            "departed": "status-departed",
            "arrived": "status-arrived",
            "returning": "status-returning",
            "forming": "status-pending",
            "loaded": "status-loaded",
        }.get(t.get("status", ""), "status-pending")

        trains_html += f"""
        <div class="train-card">
            <div class="train-header">
                <span class="train-icon">{"🚂" if t["type"]=="集装箱" else "🚃"}</span>
                <strong>{t["train_id"]}</strong>
                <span class="status-badge {status_color}">{status}</span>
            </div>
            <div class="train-detail">
                <span>第{t["current_round"]}轮</span>
                <span>{lr.get("wagon_count", total or "?")}车 / {lr.get("container_count", "?")}箱</span>
                {f'<span>到站: {t["destination_line"]}</span>' if t.get("destination_line") else ""}
            </div>
            <div class="tracking-status">
                <div class="ts-row"><span class="ts-label">95306 状态：</span><strong class="ts-value">{status}</strong></div>
                {f'<div class="ts-row"><span class="ts-label">当前位置：</span><span class="ts-value">{location}</span></div>' if location else ""}
                {f'<div class="ts-row"><span class="ts-label">最新轨迹时间：</span><span class="ts-value">{latest_time}</span></div>' if latest_time else ""}
                {f'<div class="ts-row"><span class="ts-label">发车时间：</span><span class="ts-value">{depart_at}</span></div>' if depart_at else ""}
                <div class="ts-row"><span class="ts-label">已跟踪：</span><span class="ts-value">{total if total else "?"}</span>
                <span class="ts-label">已发车：</span><span class="ts-value">{departed}</span>
                <span class="ts-label">已到达：</span><span class="ts-value">{arrived}</span></div>
            </div>
            <div class="train-timeline">
                {f'<span>发车: {lr["depart_time"][:16]}</span>' if lr.get("depart_time") else ""}
                {f'<span>到站: {lr["arrive_time"][:16]}</span>' if lr.get("arrive_time") else ""}
                {f'<span class="note">{lr.get("notes", "")}</span>' if lr.get("notes") else ""}
            </div>
        </div>"""

    # ── 散粮一次性 ──
    bulk_html = ""
    for s in one_time:
        bulk_html += f"""
        <div class="bulk-card">
            <span class="bulk-icon">📦</span>
            <strong>散粮一次性发运</strong>
            <span>{s.get("quantity", 0)}车</span>
            <span>时间: {s.get("event_time", "")}</span>
            <span class="note">{s.get("notes", "")}</span>
        </div>"""
    if not bulk_html:
        bulk_html = '<div class="bulk-card empty">暂无散粮一次性发运记录</div>'

    # ── 资源池 ──
    cp = resource_pool.get("container", {}) or {}
    wp = resource_pool.get("wagon", {}) or {}
    pool_html = f"""
    <div class="pool-col">
        <div class="pool-title">📦 箱资源</div>
        <div class="pool-row"><span class="ts-label">总量：</span><span class="ts-value">{cp.get("current_total", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">在途：</span><span class="ts-value">{cp.get("in_use_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">可用：</span><span class="ts-value">{cp.get("available_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">维修：</span><span class="ts-value">{cp.get("in_repair_qty", 0)}</span></div>
    </div>
    <div class="pool-col">
        <div class="pool-title">🚃 车体资源</div>
        <div class="pool-row"><span class="ts-label">总量：</span><span class="ts-value">{wp.get("current_total", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">在途/到站：</span><span class="ts-value">{wp.get("in_use_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">可用：</span><span class="ts-value">{wp.get("available_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">维修：</span><span class="ts-value">{wp.get("in_repair_qty", 0)}</span></div>
    </div>"""

    # ── 三账校验 ──
    snapshots = three_acc.get("snapshots", [])
    flows = three_acc.get("flows", [])
    adjustments = three_acc.get("adjustments", [])
    three_html = f"""
    <div class="three-col">
        <div class="three-title">📸 Snapshot</div>
        <div class="three-count">{len(snapshots)} 条记录</div>
        {''.join(f'<div class="three-item">{s.get("snapshot_date","")} — {s.get("source","")}</div>' for s in snapshots[:3])}
    </div>
    <div class="three-col">
        <div class="three-title">📊 Flow</div>
        <div class="three-count">{len(flows)} 条记录</div>
        {''.join(f'<div class="three-item">{f.get("flow_date","")} — {f.get("source","")}</div>' for f in flows[:3])}
    </div>
    <div class="three-col">
        <div class="three-title">🔧 Adjustment</div>
        <div class="three-count">{len(adjustments)} 条记录</div>
        {''.join(f'<div class="three-item">{a.get("adj_date","")} — {a.get("adj_type","")} × {a.get("quantity","")}</div>' for a in adjustments[:3])}
    </div>"""

    # ── 预警 ──
    warnings_html = ""
    for w in warnings:
        sev = w.get("severity", "info")
        icon = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(sev, "⚪")
        warnings_html += f"""
        <div class="warning-item warning-{sev}">
            <span>{icon}</span>
            <strong>{w.get("warning_type", "")}</strong>
            <span>{w.get("message", "")}</span>
            <span class="note">{w.get("warning_time", "")}</span>
        </div>"""

    # ── 待确认 ──
    pending_html = ""
    for p in pending:
        icon = "✅" if p.get("confirmed") else "⏳"
        pending_html += f"""
        <div class="pending-item">
            <span>{icon}</span>
            <span>{p.get("question", "")}</span>
        </div>"""

    # ── 发运计划 ──
    plan_details = plan_info.get("details", {}) if plan_info else {}
    plan_container = plan_details.get("container", {})
    plan_bulk = plan_details.get("bulk_wagon", {})
    plan_consumption = plan_details.get("factory_consumption", "")

    plan_days_rows = ""
    for pd in plan_days:
        plan_days_rows += (
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>"
        ).format(
            pd.get("day_of_week", ""),
            pd.get("container_trains", 0),
            pd.get("container_wagons", 0),
            pd.get("bulk_grain_wagons", 0),
            pd.get("estimated_tons", 0),
        )

    # Pre-compute dynamic HTML fragments to avoid nested f-string issues
    _plan_name = ""
    if plan_info:
        _plan_name = '<div class="inv-row"><strong>计划名称：</strong>{}</div>'.format(plan_info.get("name", "未命名"))
    _plan_eff = ""
    if plan_info and plan_info.get("effective_from"):
        _plan_eff = '<div class="inv-row"><strong>生效：</strong>{}</div>'.format(plan_info["effective_from"])
    _plan_ct = ""
    if plan_container:
        _plan_ct = '<div class="inv-row"><strong>集装箱：</strong>{}</div>'.format(plan_container.get("frequency_label", "待配置"))
    _plan_bk = ""
    if plan_bulk:
        _plan_bk = '<div class="inv-row"><strong>散粮：</strong>{}</div>'.format(plan_bulk.get("frequency_label", "按需或暂停"))
    _plan_cons = ""
    if plan_consumption:
        _plan_cons = '<div class="inv-row"><strong>厂耗：</strong>{} 吨/日</div>'.format(plan_consumption)
    _plan_table = ""
    if plan_days_rows:
        _plan_table = (
            '<div class="inv-row" style="margin-top:6px;"><strong>按日明细</strong></div>'
            "<table><tr><th>日</th><th>箱列</th><th>箱车</th><th>散粮车</th><th>预计吨</th></tr>{}</table>"
        ).format(plan_days_rows)

    # ── 库存 ──
    inv_level = "below_redline" if inv.get("below_red_line") else "normal"
    inv_icon = "🔴" if inv.get("below_red_line") else "🟢"

    _inv_stock = ""
    if inv:
        _inv_stock = (
            '<div class="inv-level {level}">{icon} 库存水平 {stock} 吨'
            '{warn}</div>'
        ).format(
            level=inv_level,
            icon=inv_icon,
            stock=inv.get("closing_stock", "?"),
            warn=' <span style="color:#ffcdd2;">&#x26A0;&#xFE0F; 低于红线 {} 吨</span>'.format(inv.get("red_line", "?"))
            if inv.get("below_red_line") else "",
        )
        _inv_meta = '<div class="inv-detail"><span>红线: {} 吨</span><span>可支撑: {} 天</span></div>'.format(
            inv.get("red_line", "?"), inv.get("days_supported", "?")
        )
        _inv_flow = '<div class="inv-detail"><span>本线入库: {} 吨</span><span>其他来源: {} 吨</span><span>消耗: {} 吨</span></div>'.format(
            inv.get("line_in_qty", 0), inv.get("other_source_in_qty", 0), inv.get("consumption", 0)
        )
        _inv_other = ""
        if inv.get("other_source_inferred"):
            _inv_other = '<div class="inv-row">反推其他来源: {:.0f} 吨 <span class="note">(期末 - 期初 - 本线入库 + 消耗 &#xB1; 调整)</span></div>'.format(
                inv.get("other_source_inferred", 0)
            )
        _inv_dq = '<div class="inv-row" style="margin-top:4px;"><span class="note">数据质量: {}</span></div>'.format(
            inv.get("data_quality", "unknown")
        )
    else:
        _inv_stock = ""
        _inv_meta = ""
        _inv_flow = ""
        _inv_other = ""
        _inv_dq = ""

    # ── Build HTML body in segments ──
    _header = f"""<h1>🌱 九三大豆循环运输看板</h1>
<div class="meta">
    <span>生成时间: {now}</span> |
    <span>架构: V3 循环运输资源账</span> |
    <span>数据: jiusan_cycle.db + 95306</span>
</div>"""

    _summary_block = f"""<div class="card">
    <h2>📊 今日摘要</h2>
    <div style="font-size:0.9em; line-height:1.8;">
        <div>今日发出: <strong>{dispatched}</strong> 车</div>
        <div>在途: <strong>{on_way}</strong> 车</div>
        <div>散粮一次性: {summary.get("bulk_dispatch_once", [0])[0]} 车</div>
        <div class="note" style="margin-top:4px;">数据来源: 95306 实时状态，无预期冲突检测</div>
    </div>
</div>"""

    _plan_block = f"""<div class="card">
    <h2>📋 当前发运计划</h2>
    {_plan_name}
    {_plan_eff}
    {_plan_ct}
    {_plan_bk}
    {_plan_cons}
    {_plan_table}
</div>"""

    _trains_block = f"""<div class="card full">
    <h2>🚂 循环列状态</h2>
    <div class="note" style="margin-bottom:6px;">运行状态直接来源于 95306 最新事件，无冲突检测</div>
    {trains_html}
</div>"""

    _tracking_block = f"""<div class="card full">
    <h2>🛤️ 95306 发运 / 在途轨迹</h2>
    {f'<div class="inv-row">今天 95306 已发车: {dispatched} 车</div>' if dispatched else '<div class="inv-row">今天暂无发车记录</div>'}
    {f'<div class="inv-row">当前在途: {on_way} 车</div>' if on_way else '<div class="inv-row">暂无在途车辆</div>'}
    {f'<div class="inv-row">即将到达: {soon_arrive} 车</div>' if soon_arrive else ''}
    <div class="tracking-status" style="margin-top:6px;">
        <div class="ts-row"><span class="ts-label">数据来源：</span><span class="ts-value">rail95306-sync (只读)</span></div>
        <div class="ts-row"><span class="ts-label">状态映射：</span><span class="ts-value">95306 已发车(40) → 在途 · 已到达(60) → 已到站 · 已交付(80) → 已交付</span></div>
        <div class="ts-row"><span class="ts-label">冲突检测：</span><span class="ts-value">❌ 已移除 — 运行状态以 95306 为准</span></div>
    </div>
</div>"""

    _three_block = f"""<div class="card full">
    <h2>✅ 状态 / 流量 / 调整三账</h2>
    <div class="three-grid">
        {three_html}
    </div>
    <div class="note" style="margin-top:8px;">完整三账校验请运行: python3 scripts/jiusan_three_account_verify.py</div>
</div>"""

    _efficiency_block = f"""<div class="card">
    <h2>📈 每日效率统计</h2>
    <div class="inv-row">本日发出: {dispatched} 车</div>
    <div class="inv-row">在途: {on_way} 车</div>
    <div class="inv-row">散粮一次性: {summary.get("bulk_dispatch_once", [0])[0]} 车</div>
    <div class="inv-row">三日达成率: <span class="note">待多日数据积累后计算</span></div>
</div>"""

    _warning_block = f"""<div class="card">
    <h2>⚠️ 预警与待确认</h2>
    {warnings_html if warnings_html else '<div class="note">暂无活跃预警</div>'}
    <div style="margin-top:8px;">
        <div class="pending-title" style="color:#4fc3f7; font-size:0.9em; margin-bottom:4px;">⏳ 待人工确认</div>
        {pending_html}
    </div>
</div>"""

    _bulk_block = f"""<div class="card">
    <h2>📦 非循环发运</h2>
    {bulk_html}
</div>"""

    _inv_block = f"""<div class="card">
    <h2>🏭 厂家库存与风险</h2>
    {_inv_stock}
    {_inv_meta}
    {_inv_flow}
    {_inv_other}
    {_inv_dq}
</div>"""

    _pool_block = f"""<div class="card full">
    <h2>📦 资源池分布</h2>
    <div class="pool-grid">
        {pool_html}
    </div>
</div>"""

    _footer = f"""<div class="meta" style="margin-top:20px;">
    数据来源: {', '.join(meta.get('data_sources', []))} |
    架构: {meta.get('architecture', '')} |
    最近扫描: {dashboard.get('95306_status', {}).get('last_scan', 'N/A')}
</div>
<div class="meta" style="margin-top:4px; border:1px solid #4fc3f7; padding:8px 12px; border-radius:6px; background:#0d2a3a;">
    <strong>🔄 刷新方式：手动</strong> — 执行以下命令后刷新浏览器：<br>
    <code style="display:block; margin:4px 0 0 20px; font-size:0.85em;">
    cd {REPO_ROOT}<br>
    python3 scripts/jiusan_refresh_board.py
    </code>
</div>"""

    html_body = (
        _header
        + '<div class="grid">'
        + _summary_block
        + _plan_block
        + _pool_block
        + _trains_block
        + _tracking_block
        + _three_block
        + _efficiency_block
        + _warning_block
        + _bulk_block
        + _inv_block
        + "</div>"
        + _footer
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>九三大豆循环运输看板</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0f1923; color: #e0e0e0; padding: 20px; }}
h1 {{ font-size: 1.5em; margin-bottom: 8px; color: #fff; }}
.meta {{ font-size: 0.85em; color: #8899aa; margin-bottom: 20px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-width: 1200px; }}
.card {{ background: #1a2633; border-radius: 8px; padding: 16px; border: 1px solid #2a3a4a; }}
.card h2 {{ font-size: 1em; color: #4fc3f7; margin-bottom: 12px; border-bottom: 1px solid #2a3a4a; padding-bottom: 8px; }}
.card.full {{ grid-column: 1 / -1; }}
.train-card {{ background: #1e3040; border-radius: 6px; padding: 12px; margin-bottom: 8px; }}
.train-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
.train-icon {{ font-size: 1.2em; }}
.status-badge {{ font-size: 0.78em; padding: 2px 8px; border-radius: 10px; }}
.status-returning {{ background: #f9a825; color: #1a2633; }}
.status-returned {{ background: #4caf50; color: #1a2633; }}
.status-loaded {{ background: #4fc3f7; color: #1a2633; }}
.status-departed {{ background: #ff9800; color: #1a2633; }}
.status-arrived {{ background: #66bb6a; color: #1a2633; }}
.status-pending {{ background: #78909c; color: #fff; }}
.status-unloaded {{ background: #66bb6a; color: #1a2633; }}
.train-detail {{ display: flex; gap: 16px; font-size: 0.85em; color: #b0bec5; margin-bottom: 4px; }}
.train-timeline {{ font-size: 0.82em; color: #78909c; display: flex; gap: 12px; flex-wrap: wrap; }}
.tracking-status {{ font-size: 0.82em; color: #b3e5fc; background: #0d2a3a; border-radius: 4px; padding: 6px 10px; margin: 4px 0; line-height: 1.6; }}
.ts-row {{ margin: 1px 0; }}
.ts-label {{ color: #78909c; }}
.ts-value {{ color: #e0e0e0; }}
.pool-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.pool-col {{ background: #1e3040; border-radius: 6px; padding: 10px; }}
.pool-title {{ font-weight: bold; color: #4fc3f7; margin-bottom: 6px; }}
.pool-row {{ font-size: 0.85em; line-height: 1.7; }}
.bulk-card {{ background: #2a1a20; border-radius: 6px; padding: 10px; margin-bottom: 6px;
             display: flex; gap: 12px; align-items: center; font-size: 0.85em; flex-wrap: wrap; }}
.bulk-icon {{ font-size: 1.1em; }}
.empty {{ color: #78909c; font-style: italic; }}
.note {{ color: #78909c; font-size: 0.85em; }}
.three-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }}
.three-col {{ background: #1e3040; border-radius: 6px; padding: 10px; }}
.three-title {{ color: #4fc3f7; font-weight: bold; margin-bottom: 4px; }}
.three-count {{ font-size: 0.85em; color: #78909c; margin-bottom: 6px; }}
.three-item {{ font-size: 0.82em; padding: 2px 0; color: #b0bec5; }}
.warning-item {{ display: flex; gap: 8px; align-items: center; font-size: 0.85em; padding: 6px 8px; margin: 4px 0; border-radius: 4px; }}
.warning-critical {{ background: #2a1a1a; border-left: 3px solid #e53935; }}
.warning-warning {{ background: #2a2a1a; border-left: 3px solid #ff9800; }}
.warning-info {{ background: #1a2a2a; border-left: 3px solid #4fc3f7; }}
.pending-item {{ display: flex; gap: 8px; align-items: center; font-size: 0.85em; padding: 4px 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
th {{ text-align: left; color: #4fc3f7; padding: 6px 8px; border-bottom: 1px solid #2a3a4a; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #1e3040; }}
.inv-level {{ font-size: 1.2em; }}
.inv-detail {{ display: flex; gap: 20px; flex-wrap: wrap; font-size: 0.9em; margin-top: 8px; }}
.inv-detail span {{ white-space: nowrap; }}
.inv-row {{ font-size: 0.85em; line-height: 1.6; }}
.normal {{ color: #c8e6c9; }}
.below_redline {{ color: #ffcdd2; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} .pool-grid, .three-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    return html


def main():
    import sys
    conn = get_conn()

    print("生成 V3 九三大豆循环运输看板...")
    print(f"  DB: {JIUSAN_DB}")
    dashboard = generate(conn)
    conn.close()

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = DASHBOARD_DIR / "jiusan_dashboard_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)
    print(f"  JSON: {json_path}")

    if "--example" in sys.argv:
        example_path = DASHBOARD_DIR / "jiusan_dashboard_data.example.json"
        with open(example_path, "w", encoding="utf-8") as f:
            json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)
        print(f"  Example: {example_path}")

    # HTML
    html_content = generate_html(dashboard)
    html_path = DASHBOARD_DIR / "jiusan_dashboard.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  HTML: {html_path}")

    print("✅ V3 看板生成完成")
    print("  运行状态直接映射 95306，无冲突检测")
    print(f"  列二状态 = {dashboard.get('cycle_trains', [{}])[1].get('tracking', {}).get('summary_status', 'N/A')}")


if __name__ == "__main__":
    main()
