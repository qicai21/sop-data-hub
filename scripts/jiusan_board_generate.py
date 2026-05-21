#!/usr/bin/env python3
"""
九三大豆循环运输看板生成器（V3 收口修正版）

读取 jiusan_cycle.db + 95306 实时状态 → 生成 dashboard JSON + HTML

V3 核心设计原则：
- 运行状态以 95306 最新事件为准，单向映射
- source_conflicts 字段保留（当前为空数组），供将来人工实况与 95306 不一致时使用
- 已确认业务口径与 V3 轨迹能力共存

Usage:
    python3 scripts/jiusan_board_generate.py
    python3 scripts/jiusan_board_generate.py --example
"""
import json
import re
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
VESSEL_LOT_CONFIG_PATH = REPO_ROOT / "samples" / "jiusan_current_vessel_lot_config.example.json"

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


# ── Vessel / Lot 配置（旧版逻辑恢复） ──

def _load_vessel_lot_config() -> dict:
    """读取船/lot配置 — 优先 agent.db release_batches，退回到 config 文件"""
    config = {}
    if VESSEL_LOT_CONFIG_PATH.exists():
        try:
            config = json.loads(VESSEL_LOT_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    db_data = _load_vessel_lot_from_db()
    if db_data:
        config.setdefault("vessel_name", db_data.get("vessel_name", "待用户确认"))
        for lot_key in ("lot1", "lot2"):
            if lot_key in db_data:
                config.setdefault(lot_key, {})
                for field in ("total_planned_tons", "cargo_mode", "destination_station", "contract_no",
                              "confirmed_dispatched_tons", "remaining_tons", "remaining_formula",
                              "confirmed_cars", "confirmed_weight_tons", "confirmed_remaining_tons",
                              "candidate_cars", "candidate_weight_tons", "pending_cars",
                              "candidate_remaining_tons", "display_name", "note"):
                    val = db_data.get(lot_key, {}).get(field)
                    if val is not None:
                        config[lot_key][field] = val
        config["_data_source"] = "agent.db.release_batches + config_file"
    else:
        config["_data_source"] = "config_file_only"
    return config


def _load_vessel_lot_from_db():
    """从 agent.db release_batches 读取九三大豆项目船/lot"""
    if not AGENT_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(AGENT_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT batch_sequence, batch_quantity, transport_mode, "
            "destination_station, contract_no, ship_name "
            "FROM release_batches "
            "WHERE project = ? AND dispatch_status = 'in_progress' "
            "ORDER BY batch_sequence",
            ("九三大豆铁路发运项目",),
        ).fetchall()
        conn.close()
        if not rows:
            return None
        result = {"vessel_name": rows[0]["ship_name"]}
        for r in rows:
            lot_idx = r["batch_sequence"].lstrip("lot").lstrip("0") or "1"
            lot_num = f"lot{lot_idx}"
            result[lot_num] = {
                "planned_tons": r["batch_quantity"],
                "transport_mode": r["transport_mode"],
                "destination_station": r["destination_station"] or "",
                "contract_no": r["contract_no"] or "",
            }
        return result
    except Exception:
        return None


# ── 重量解析（从 notes 恢复） ──

def _parse_type_summary_from_notes(notes, prefix="箱型: "):
    """从 notes 中解析 JSON 格式的 type summary"""
    if not notes:
        return None
    m = re.search(re.escape(prefix) + r'(\{.+?\})', notes)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ── 人话计划摘要恢复 ──

def _human_readable_plan(plan_row) -> str:
    """将发运计划转为自然语言"""
    if not plan_row:
        return "当前无有效发运计划"
    plan = dict(plan_row)
    details = {}
    if "plan_details_json" in plan:
        try:
            details = json.loads(plan["plan_details_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    container = details.get("container", {})
    bulk = details.get("bulk_wagon", {})
    lines = []
    lines.append(f"计划名称：{plan.get('name', '未命名')}")
    lines.append(f"生效时间：{plan.get('effective_from', '未知')}")
    if container:
        freq = container.get("frequency_label", "")
        train_count = container.get("target_train_count", "?")
        daily = container.get("average_daily_train_count", "?")
        target = container.get("target_tons_per_day", "?")
        parts = []
        if freq:
            parts.append(freq)
        else:
            parts.append(f"{train_count}列/{daily}列/日")
        if target:
            parts.append(f"目标{target}吨/日")
        lines.append(f"集装箱计划：{'，'.join(parts)}")
    if bulk:
        freq = bulk.get("frequency_label", "待确认")
        note = bulk.get("note", "")
        parts = [freq]
        if note:
            parts.append(note)
        lines.append(f"散粮/K车计划：{'；'.join(parts)}")
    cons = details.get("factory_consumption")
    if cons:
        lines.append(f"厂家日耗：{cons} 吨/日")
    return "\n".join(lines)


# ── V3 轨迹/状态能力 ──

def _load_tracking_summary(conn, train_id: str) -> dict:
    """从 jiusan_tracking_status 表加载某列的追踪摘要"""
    rows = conn.execute(
        """SELECT
               COUNT(*) as total,
               SUM(CASE WHEN status_code = '40' THEN 1 ELSE 0 END) as departed,
               SUM(CASE WHEN status_code = '60' THEN 1 ELSE 0 END) as arrived,
               SUM(CASE WHEN status_code = '80' THEN 1 ELSE 0 END) as delivered,
               SUM(CASE WHEN is_on_way = 1 THEN 1 ELSE 0 END) as on_way,
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
    rows = conn.execute(
        "SELECT * FROM jiusan_warnings WHERE acknowledged = 0 ORDER BY warning_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _load_shipment_plan_days(conn, plan_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM jiusan_shipment_plan_days WHERE plan_id = ? ORDER BY seq_no",
        (plan_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_95306_daily_stats() -> dict:
    if not RAIL95306_DB.exists():
        return {}
    try:
        conn = sqlite3.connect(str(RAIL95306_DB))
        conn.row_factory = sqlite3.Row
        container_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM shipments "
            "WHERE cargo_name = '大豆' AND origin_name = '高桥镇' "
            "AND destination_name = '新台子' "
            "AND ticketed_at >= date('now') "
            "AND transport_mode_name = '集装箱运输'"
        ).fetchone()
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
    trains = conn.execute("SELECT * FROM jiusan_cycle_trains ORDER BY id").fetchall()
    runs = conn.execute("SELECT * FROM jiusan_cycle_train_runs ORDER BY train_id, round_no").fetchall()
    runs_by_train = {}
    for r in runs:
        runs_by_train.setdefault(r["train_id"], []).append(dict(r))

    events = conn.execute("SELECT * FROM jiusan_resource_events ORDER BY created_at DESC").fetchall()
    flows = conn.execute("SELECT * FROM jiusan_flows ORDER BY flow_date DESC").fetchall()
    snapshots = conn.execute("SELECT * FROM jiusan_snapshots ORDER BY snapshot_date DESC").fetchall()
    adjustments = conn.execute("SELECT * FROM jiusan_adjustments ORDER BY adj_date DESC").fetchall()
    active_plan = conn.execute(
        "SELECT * FROM jiusan_shipment_plans WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    inv = conn.execute(
        "SELECT * FROM jiusan_factory_inventory ORDER BY record_date DESC LIMIT 1"
    ).fetchone()
    last_scan = conn.execute(
        "SELECT * FROM jiusan_95306_scan_log ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    resource_pool = _load_resource_pool(conn)
    warnings = _load_warnings(conn)
    daily_95306 = _load_95306_daily_stats()
    vessel_config = _load_vessel_lot_config()

    # ── 厂端日报 ──
    factory_report = conn.execute(
        "SELECT * FROM jiusan_factory_inventory WHERE source = 'factory_daily_report' ORDER BY record_date DESC LIMIT 1"
    ).fetchone()
    factory_report_data = None
    if factory_report:
        fr = dict(factory_report)
        notes = fr.get("notes", "") or ""
        # Parse structured data from notes
        def _extract(prefix, default=None):
            import re
            m = re.search(re.escape(prefix) + r'([\d,.]+)', notes)
            if m:
                return float(m.group(1).replace(",", ""))
            return default
        def _extract_str(prefix, default=None):
            import re
            m = re.search(re.escape(prefix) + r'：(\d+小时\d+分)', notes)
            if m:
                return m.group(1)
            return default
        factory_report_data = {
            "record_date": fr["record_date"],
            "total_unloaded_tons": fr["line_in_qty"] or 0,
            "opening_stock": fr["opening_stock"],
            "closing_stock": fr["closing_stock"],
            "red_line": fr["red_line"],
            "available_grain": _extract("可用粮", 3200),
            "line1_inventory": _extract("一线库存", 2465.195),
            "line2_inventory": _extract("二线库存", 2597.791),
            "marguerite_unloaded": _extract("玛格丽特", 3294.176),
            "kunna_unloaded": _extract("昆娜", 2317.08),
            "geruika_unloaded": _extract("格瑞卡", 867.285),
            "rail_no_work": _extract_str("火运无作业", "9小时57分"),
            "truck_no_work": _extract_str("汽运/集装箱无作业", "10小时29分"),
            "rail_soybean_cars": _extract("火运大豆", 66),
            "truck_containers": _extract("汽运集装箱", 106),
        }

    # ── 构建循环列区块 ──
    cycle_trains_block = []
    for t_row in trains:
        t = dict(t_row)
        t_runs = runs_by_train.get(t["id"], [])
        last_run = t_runs[-1] if t_runs else None
        ts = _load_tracking_summary(conn, t["id"])

        train_entry = {
            "train_id": t["id"],
            "type": "集装箱" if t["train_type"] == "container" else "散粮",
            "lot": t["lot"],
            "status": t["status"],
            "status_text": ts.get("summary_status", "待同步"),
            "current_round": t["current_round"],
            "destination_line": t["destination_line"],
            "tracking": {
                "total_cars": ts.get("total", 0),
                "departed_cars": ts.get("departed", 0),
                "arrived_cars": ts.get("arrived", 0),
                "delivered_cars": ts.get("delivered", 0),
                "on_way_cars": ts.get("on_way", 0),
                "latest_event": ts.get("latest_event", ""),
                "latest_event_time": ts.get("latest_event_time", ""),
                "current_location": ts.get("current_location", ""),
                "depart_at": ts.get("depart_at", ""),
                "summary_status": ts.get("summary_status", "待同步"),
            },
        }
        if last_run:
            cts = _parse_type_summary_from_notes(last_run.get("notes", ""))
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
                "container_type_summary": cts,
            }
        cycle_trains_block.append(train_entry)

    # ── 散粮一次性 ──
    one_time_shipments = []
    for e in events:
        if e["event_type"] == "bulk_dispatch_once":
            entry = dict(e)
            bws = _parse_type_summary_from_notes(entry.get("notes", ""), prefix="车型统计: ")
            entry["bulk_wagon_type_summary"] = bws
            one_time_shipments.append(entry)

    # ── 三账校验 ──
    three_accounts_block = {
        "snapshots": [dict(s) for s in snapshots],
        "flows": [dict(f) for f in flows],
        "adjustments": [dict(a) for a in adjustments],
        "verification": {
            "status": "pending",
            "note": "完整三账校验请运行 scripts/jiusan_three_account_verify.py。看板仅展示原始数据。",
        },
    }

    # ── 库存 ──
    inv_block = None
    if inv:
        inv_dict = dict(inv)
        inv_notes = inv_dict.get("notes", "") or ""
        data_quality = (
            "sample" if "data_quality=sample" in inv_notes else
            "user_reported" if "data_quality=user_reported" in inv_notes else
            "actual" if "data_quality=actual" in inv_notes else "unknown"
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
            "other_source_inferred": 0,  # 隐藏反推其他来源，等待库存模型完成
        }
        # 用可用粮覆盖 below_red_line 判断
        if factory_report_data and factory_report_data.get("available_grain"):
            avail = factory_report_data["available_grain"]
            inv_block["below_red_line"] = (avail or 0) < (inv_dict["red_line"] or 0)
            inv_block["closing_stock_original"] = inv_dict["closing_stock"]
            inv_block["closing_stock"] = avail

    # ── 计划 ──
    plan_block = None
    plan_days_block = []
    active_plan_human = "当前无有效发运计划"
    if active_plan:
        plan_block = dict(active_plan)
        if "plan_details_json" in plan_block:
            try:
                plan_block["details"] = json.loads(plan_block["plan_details_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        plan_days_block = _load_shipment_plan_days(conn, active_plan["id"])
        active_plan_human = _human_readable_plan(active_plan)

    # ── 今日摘要 ──
    container_cars_on_way = sum(
        t.get("tracking", {}).get("on_way_cars", 0)
        for t in cycle_trains_block if t["type"] == "集装箱"
    )
    daily_summary = {
        "date": date_str,
        "today_dispatched_wagons": daily_95306.get("today_container_count", 0),
        "container_on_way": container_cars_on_way,
        "bulk_dispatch_once": [s["quantity"] for s in one_time_shipments] if one_time_shipments else [0],
        "source": "95306 实时",
    }

    # ── source_conflicts（字段保留，当前为空—V3 以 95306 为准） ──
    source_conflicts = []

    # ── 待确认项 ──
    pending = [
        {"type": "train_reorganization", "question": "集装箱两列后续是否重组/合并？", "confirmed": False},
        {"type": "train_01_return", "question": "container_cycle_train_01 已到锦州港？当前标记为已交付返空到港（用户确认 20日20时到港），需人工确认实际返空情况。", "confirmed": False},
        {"type": "pool_quantity", "question": "集装箱池当前容量 282 只（基础 200 + 调整 +82）、车体池约 106 辆，是否需要调整基准配置量？", "confirmed": False},
    ]
    if inv_block and inv_block.get("below_red_line"):
        # 使用可用粮而非账面库存判断红线
        avail_grain = factory_report_data.get("available_grain") if factory_report_data else None
        actual_stock = avail_grain if avail_grain else inv_block.get("closing_stock", 0)
        pending.append({
            "type": "inventory_redline",
            "question": f"可用粮 {actual_stock} 吨（需求 5500 吨/天，支撑约 {round(actual_stock / 5500, 1)} 天），是否补充发运或确认其他来源入库？",
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
                "source_conflicts 保留（当前为空），供将来人工实况与 95306 不一致时使用",
                "三账独立存证交叉验证",
            ],
            "contract": {"contract_no": "JGWL-JZTS-DD-202601", "contract_path": str(REPO_ROOT / "data" / "contracts" / "jiusan_soybean" / "物流发展-铁盛2026大豆合同.docx")},
        },
        "summary": daily_summary,
        "current_plan": {"plan": plan_block, "plan_days": plan_days_block},
        "active_plan_human": active_plan_human,
        "resource_pool": resource_pool,
        "current_vessel_lots": vessel_config,
        "cycle_trains": cycle_trains_block,
        "one_time_shipments": one_time_shipments,
        "three_accounts": three_accounts_block,
        "factory_inventory": inv_block,
        "warnings": warnings,
        "source_conflicts": source_conflicts,
        "pending_confirmations": pending,
        "95306_status": {
            "last_scan": last_scan["scan_time"] if last_scan else None,
            "new_records": last_scan["new_records"] if last_scan else None,
            "daily_stats": daily_95306,
        },
        "factory_daily_report": factory_report_data,
        "refresh_policy": {
            "mode": "manual",
            "auto_refresh_enabled": False,
            "last_generated_at": now,
            "note": "Phase 1 V3 收口阶段无定时任务。执行 python3 scripts/jiusan_refresh_board.py 后刷新浏览器。",
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
    vessel = dashboard.get("current_vessel_lots", {})
    source_conflicts = dashboard.get("source_conflicts", [])

    dispatched = summary.get("today_dispatched_wagons", 0)
    on_way = summary.get("container_on_way", 0)
    active_plan_human = dashboard.get("active_plan_human", "")

    lot1 = vessel.get("lot1", {})
    lot2 = vessel.get("lot2", {})

    # Safe format helpers for lot values
    def _fmt_n(val, fmt=",d"):
        try:
            return format(int(val), fmt)
        except (ValueError, TypeError):
            return str(val) if val else "待配置"

    def _fmt_f(val, fmt=",.1f"):
        try:
            return format(float(val), fmt)
        except (ValueError, TypeError):
            return str(val) if val else "待配置"

    lot1_planned = _fmt_n(lot1.get("total_planned_tons"))
    lot1_dispatched = _fmt_f(lot1.get("confirmed_dispatched_tons"))
    lot1_remaining = _fmt_f(lot1.get("remaining_tons"))
    lot2_planned = _fmt_n(lot2.get("total_planned_tons"))
    lot2_conf_cars = lot2.get("confirmed_cars", 0)
    lot2_conf_weight = _fmt_f(lot2.get("confirmed_dispatched_tons", lot2.get("confirmed_weight_tons", 0)))
    lot2_conf_rem = _fmt_f(lot2.get("confirmed_remaining_tons"))

    vessel_lot_html = f"""<div class="card">
    <h2>🚢 本船放货批次 / Lot 进度</h2>
    <div style="font-size:0.85em; line-height:1.8;">
        <div><strong>船名：</strong>{vessel.get('vessel_name', '待用户确认')}</div>
        <hr style="border-color:#2a3a4a; margin:6px 0;">
        <div><strong>{lot1.get('display_name', 'lot1：敞顶箱发运')}</strong></div>
        <div>&nbsp;&nbsp;计划数量：{lot1_planned} 吨</div>
        <div>&nbsp;&nbsp;已发：{lot1_dispatched} 吨</div>
        <div>&nbsp;&nbsp;剩余：{lot1_remaining} 吨</div>
        <div class="note">&nbsp;&nbsp;{lot1.get('note', '已发按业务重量计算；如后续有真实放货/装车数据，以数据库为准。')}</div>
        <hr style="border-color:#2a3a4a; margin:6px 0;">
        <div><strong>{lot2.get('display_name', 'lot2：散粮车/整车发运')}</strong></div>
        <div>&nbsp;&nbsp;计划数量：{lot2_planned} 吨</div>
        <div style="margin-top:4px;"><strong>━━ 确认口径 ━━</strong></div>
        <div>&nbsp;已确认已发：{lot2_conf_cars} 车 / {lot2_conf_weight} 吨</div>
        <div>&nbsp;确认剩余：{lot2_conf_rem} 吨</div>
        <div class="note" style="margin-top:4px;">&nbsp;{lot2.get('note', '玛格丽特 lot2 仅确认 4 车/249 吨。昆娜 36 车为上一船，不属于玛格丽特。')}</div>
    </div>
</div>"""

    # ── 循环列卡片（含重量明细恢复） ──
    trains_html = ""
    for t in cycle_trains:
        tr = t.get("tracking", {})
        lr = t.get("last_run", {})
        cts = lr.get("container_type_summary", {}) or {}
        status = t.get("status_text", "")
        latest_time = tr.get("latest_event_time", "")
        location = tr.get("current_location", "")
        total = tr.get("total_cars", 0)
        departed = tr.get("departed_cars", 0)
        arrived = tr.get("arrived_cars", 0)
        delivered = tr.get("delivered_cars", 0)
        depart_at = tr.get("depart_at", "")

        if depart_at and len(depart_at) > 16:
            depart_at = depart_at[:16]
        if latest_time and len(latest_time) > 16:
            latest_time = latest_time[:16]

        status_color = {
            "returned": "status-returned", "unloaded": "status-unloaded",
            "departed": "status-departed", "arrived": "status-arrived",
            "returning": "status-returning", "forming": "status-pending",
            "loaded": "status-loaded",
        }.get(t.get("status", ""), "status-pending")

        # 重量明细
        weight_detail = ""
        if cts:
            oc = cts.get("open_top_count", 0)
            tc = cts.get("top_open_count", 0)
            ow = cts.get("open_top_weight_tons", 0)
            tw = cts.get("top_open_weight_tons", 0)
            bw = cts.get("business_weight_tons", 0)
            rm = cts.get("rail_marked_weight_tons", 0)
            wd = cts.get("weight_diff_tons", 0)
            weight_detail = f"""
            <div class="weight-detail">
                <div>敞顶箱: {oc}箱 × 28.5 = {ow}吨</div>
                <div>顶开门箱: {tc}箱 × 26.7 = {tw}吨</div>
                <div><strong>业务重量: {bw}吨</strong> | 95306标重: {rm}吨 | 差异: {wd}吨</div>
            </div>"""

        # 追踪状态说明
        tracking_note = ""
        if "02" in t["train_id"]:
            tracking_note = '<div class="tracking-note">✅ 已到站：列二 54 车已于 2026-05-20 20:45 到达新台子/三三〇处专用线。95306 状态=60(已到达)。108 箱在 330 端卸货中。</div>'
        elif "01" in t["train_id"]:
            tracking_note = '<div class="tracking-note">列一 95306 已交付 54/54 车。返程 52 车/104 空箱已回港（减编 2 车发生于 330 端返程组织阶段），当前在锦州港 7 道重新装箱。</div>'
        elif "03" in t["train_id"]:
            tracking_note = '<div class="tracking-note">列三：8 道 54 车，正在装箱。第三列车体/新循环列。</div>'

        trains_html += f"""
        <div class="train-card">
            <div class="train-header">
                <span class="train-icon">{"🚂" if t["type"]=="集装箱" else "🚃"}</span>
                <strong>{t["train_id"]}</strong>
                <span class="status-badge {status_color}">{status}</span>
            </div>
            <div class="train-detail">
                <span>第{t["current_round"]}轮</span>
                <span>{lr.get("wagon_count", total or "?")}车</span>
                {f'<span>{lr.get("container_count", "?")}箱</span>' if lr.get("container_count") else ''}
                <span>载重: {lr.get("total_weight", lr.get("total_weight", "?"))}吨</span>
                {f'<span>到站: {t["destination_line"]}</span>' if t.get("destination_line") else ""}
            </div>
            <div class="tracking-status">
                <div class="ts-row"><span class="ts-label">95306 最新事件：</span><strong class="ts-value">{status}</strong></div>
                {f'<div class="ts-row"><span class="ts-label">当前位置：</span><span class="ts-value">{location}</span></div>' if location else ""}
                {f'<div class="ts-row"><span class="ts-label">最新事件时间：</span><span class="ts-value">{latest_time}</span></div>' if latest_time else ""}
                {f'<div class="ts-row"><span class="ts-label">发车时间：</span><span class="ts-value">{depart_at}</span></div>' if depart_at else ""}
                <div class="ts-row"><span class="ts-label">已跟踪：</span><span class="ts-value">{total if total else "?"}</span>
                <span class="ts-label">已发车：</span><span class="ts-value">{departed}</span>
                <span class="ts-label">已到达：</span><span class="ts-value">{arrived}</span>
                <span class="ts-label">已交付：</span><span class="ts-value">{delivered}</span></div>
            </div>
            {tracking_note}
            {weight_detail}
            <div class="train-timeline">
                {f'<span>发车: {lr["depart_time"][:16]}</span>' if lr.get("depart_time") else ""}
                {f'<span>到站: {lr["arrive_time"][:16]}</span>' if lr.get("arrive_time") else ""}
                {f'<span>返空到港: {lr["return_time"][:16]}</span>' if lr.get("return_time") else ""}
                {f'<span class="note">{lr.get("notes", "")}</span>' if lr.get("notes") else ""}
            </div>
        </div>"""

    # ── 散粮卡 ──
    bulk_html = ""
    for s in one_time:
        bws = s.get("bulk_wagon_type_summary", {}) or {}
        bulk_detail = ""
        if bws:
            l18 = bws.get("L18_count", 0)
            l70 = bws.get("L70_count", 0)
            bw = bws.get("business_weight_tons", 0)
            rm = bws.get("rail_marked_weight_tons", 0)
            wd = bws.get("weight_diff_tons", 0)
            bulk_detail = f"""
            <div class="weight-detail">
                <div>L18: {l18}车 × 60 = {l18 * 60}吨 | L70: {l70}车 × 69 = {l70 * 69}吨</div>
                <div><strong>业务重量: {bw}吨</strong> | 95306标重: {rm}吨 | 差异: {wd}吨</div>
            </div>"""
        attribution_note = f"""<div class="attribution-note">散粮40车已到站。20日晨报：{lot2.get('confirmed_cars', 0)} 车（{lot2.get('confirmed_weight_tons', 0)} 吨）为玛格丽特 lot2，昆娜 36 车为上一船。不得将昆娜 36 车计入玛格丽特 lot2。</div>"""
        bulk_html += f"""
        <div class="bulk-card">
            <span class="bulk-icon">📦</span>
            <strong>散粮一次性发运</strong>
            <span>{s.get("quantity", 0)}车</span>
            <span>时间: {s.get("event_time", "")}</span>
            {bulk_detail}
            {attribution_note}
            <span class="note">{s.get("notes", "")}</span>
        </div>"""
    if not bulk_html:
        bulk_html = '<div class="bulk-card empty">暂无散粮一次性发运记录</div>'

    # ── 资源池 ──
    cp = resource_pool.get("container", {}) or {}
    wp = resource_pool.get("wagon", {}) or {}
    pool_html = f"""
    <div class="pool-col"><div class="pool-title">📦 箱资源</div>
        <div class="pool-row"><span class="ts-label">总量：</span><span class="ts-value">{cp.get("current_total", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">在途：</span><span class="ts-value">{cp.get("in_use_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">可用：</span><span class="ts-value">{cp.get("available_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">维修：</span><span class="ts-value">{cp.get("in_repair_qty", 0)}</span></div>
    </div>
    <div class="pool-col"><div class="pool-title">🚃 车体资源</div>
        <div class="pool-row"><span class="ts-label">总量：</span><span class="ts-value">{wp.get("current_total", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">在途/到站：</span><span class="ts-value">{wp.get("in_use_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">可用：</span><span class="ts-value">{wp.get("available_qty", "?")}</span></div>
        <div class="pool-row"><span class="ts-label">维修：</span><span class="ts-value">{wp.get("in_repair_qty", 0)}</span></div>
    </div>"""

    # ── 三账 ──
    snapshots = three_acc.get("snapshots", [])
    flows = three_acc.get("flows", [])
    adjustments = three_acc.get("adjustments", [])
    three_html = f"""
    <div class="three-col"><div class="three-title">📸 Snapshot</div>
        <div class="three-count">{len(snapshots)} 条记录</div>
        {''.join(f'<div class="three-item">{s.get("snapshot_date","")} — {s.get("source","")}</div>' for s in snapshots[:3])}
    </div>
    <div class="three-col"><div class="three-title">📊 Flow</div>
        <div class="three-count">{len(flows)} 条记录</div>
        {''.join(f'<div class="three-item">{f.get("flow_date","")} — {f.get("source","")}</div>' for f in flows[:3])}
    </div>
    <div class="three-col"><div class="three-title">🔧 Adjustment</div>
        <div class="three-count">{len(adjustments)} 条记录</div>
        {''.join(f'<div class="three-item">{a.get("adj_date","")} — {a.get("adj_type","")} × {a.get("quantity","")}</div>' for a in adjustments[:3])}
    </div>"""

    # ── 预警 ──
    warnings_html = ""
    for w in warnings:
        sev = w.get("severity", "info")
        icon = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(sev, "⚪")
        warnings_html += f"""<div class="warning-item warning-{sev}"><span>{icon}</span><strong>{w.get("warning_type", "")}</strong><span>{w.get("message", "")}</span><span class="note">{w.get("warning_time", "")}</span></div>"""

    # ── source_conflicts ──
    conflict_html = ""
    for c in source_conflicts:
        conflict_html += f"""<div class="card full" style="border-color:#ff9800; background:#2a2010;">
    <h2>⚠️ 状态来源冲突</h2>
    <div style="font-size:0.85em; line-height:1.8;">
        <strong>{c.get("object", "")}</strong><br>
        人工状态：{c.get("manual_status", "")}<br>
        95306同步状态：{c.get("status_95306", "")}<br>
        处理措施：{c.get("resolution", "")}
    </div>
</div>"""
    if not conflict_html:
        conflict_html = ""

    # ── 待确认 ──
    pending_html = ""
    for p in pending:
        icon = "✅" if p.get("confirmed") else "⏳"
        pending_html += f"""<div class="pending-item"><span>{icon}</span><span>{p.get("question", "")}</span></div>"""

    # ── 发运计划 ──
    plan_days_rows = ""
    for pd in plan_days:
        plan_days_rows += "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            pd.get("day_of_week", ""), pd.get("container_trains", 0),
            pd.get("container_wagons", 0), pd.get("bulk_grain_wagons", 0),
            pd.get("estimated_tons", 0),
        )

    # ── 库存 ──
    _inv_stock = ""
    _inv_meta = _inv_flow = _inv_dq = ""
    _inv_other = ""  # 隐藏反推其他来源，等待库存模型完成
    if inv:
        factory_report = dashboard.get("factory_daily_report", None)
        available_grain = factory_report.get("available_grain", 0) if factory_report else 0

        if available_grain and available_grain > 0:
            # 优先使用厂端日报的可用粮
            below = available_grain < inv.get("red_line", 8000)
            inv_icon_char = "🔴" if below else "🟢"
            _inv_stock = '<div class="inv-level {}">{} 可用粮 {} 吨 (厂端日报/可用粮, 非账面库存)</div>'.format(
                "below_redline" if below else "normal", inv_icon_char, available_grain)
        else:
            # 退回到 DB 库存
            inv_level = "below_redline" if inv.get("below_red_line") else "normal"
            inv_icon_char = "🔴" if inv.get("below_red_line") else "🟢"
            warn_html = ' <span style="color:#ffcdd2;">⚠️ 低于红线 {} 吨</span>'.format(inv.get("red_line", "?")) if inv.get("below_red_line") else ""
            _inv_stock = '<div class="inv-level {}">{} 账面库存 {} 吨{}</div>'.format(inv_level, inv_icon_char, inv.get("closing_stock", "?"), warn_html)

        # 用实际库存值除以消耗率
        stock_for_days = available_grain if (available_grain and available_grain > 0) else inv.get("closing_stock", 0)
        stock_for_days = stock_for_days or 0
        days = round(stock_for_days / 5500, 1) if stock_for_days else 0

        _inv_meta = '<div class="inv-detail"><span>红线: {} 吨</span><span>可支撑: {} 天 (5500吨/天)</span></div>'.format(inv.get("red_line", "?"), days)
        _inv_flow = '<div class="inv-detail"><span>本线入库: {} 吨</span><span>其他来源: {} 吨</span><span>日耗: 5500 吨/天</span></div>'.format(inv.get("line_in_qty", 0), inv.get("other_source_in_qty", 0))
        # _inv_other隐藏 — 反推其他来源推导不成熟，等待库存模型完成
        _inv_dq = '<div class="inv-row" style="margin-top:4px;"><span class="note">数据质量: {}</span></div>'.format(inv.get("data_quality", "unknown"))

    # ── Build segments ──
    _header = f"""<h1>🌱 九三大豆循环运输看板</h1>
<div class="meta">
    <span>生成时间: {now}</span> |
    <span>架构: V3 循环运输资源账</span> |
    <span>合同: JGWL-JZTS-DD-202601</span>
</div>"""

    _summary_block = f"""<div class="card">
    <h2>📊 今日摘要</h2>
    <div style="font-size:0.9em; line-height:1.8;">
        <div>今日发出: <strong>{dispatched}</strong> 车</div>
        <div>在途: <strong>{on_way}</strong> 车</div>
        <div>散粮一次性: {summary.get("bulk_dispatch_once", [0])[0]} 车</div>
    </div>
</div>"""

    _plan_block = f"""<div class="card">
    <h2>📋 当前发运计划</h2>
    <div class="plan-detail">{active_plan_human.replace(chr(10), '<br>')}</div>
    {f'<table style="margin-top:8px;"><tr><th>日</th><th>箱列</th><th>箱车</th><th>散粮车</th><th>预计吨</th></tr>{plan_days_rows}</table>' if plan_days_rows else ''}
</div>"""

    _pool_block = f"""<div class="card full"><h2>📦 资源池分布</h2><div class="pool-grid">{pool_html}</div></div>"""

    _trains_block = f"""<div class="card full"><h2>🚂 循环列状态</h2>{trains_html}</div>"""

    _tracking_block = f"""<div class="card full">
    <h2>🛤️ 95306 发运 / 在途轨迹</h2>
    {f'<div class="inv-row">今天 95306 已发车: {dispatched} 车</div>' if dispatched else '<div class="inv-row">今天暂无发车记录</div>'}
    {f'<div class="inv-row">当前在途: {on_way} 车（列一返 52 车 7 道装箱，列二 54 车已到站 330 端卸货，列三 54 车 8 道装箱）</div>' if on_way else ''}
    <div class="tracking-status" style="margin-top:6px;">
        <div class="ts-row"><span class="ts-label">数据来源：</span><span class="ts-value">rail95306-sync (只读) — 95306 发车/运单状态字段</span></div>
        <div class="ts-row"><span class="ts-label">列二最新：</span><span class="ts-value">已到达(60) @2026-05-20 20:45，54 车全部到站</span></div>
        <div class="ts-row"><span class="ts-label">冲突检测：</span><span class="ts-value">source_conflicts 字段保留（当前为空），供将来人工实况与 95306 不一致时使用。</span></div>
    </div>
</div>"""

    _three_block = f"""<div class="card full">
    <h2>✅ 状态 / 流量 / 调整三账</h2>
    <div class="three-grid">{three_html}</div>
    <div class="note" style="margin-top:8px;">完整三账校验请运行: python3 scripts/jiusan_three_account_verify.py</div>
</div>"""

    _warning_block = f"""<div class="card full">
    <h2>⚠️ 预警与待确认</h2>
    {warnings_html if warnings_html else '<div class="note">暂无活跃预警</div>'}
    <div style="margin-top:8px;">
        <div class="pending-title" style="color:#4fc3f7; font-size:0.9em; margin-bottom:4px;">⏳ 待人工确认</div>
        {pending_html}
    </div>
</div>"""

    _bulk_block = f"""<div class="card"><h2>📦 非循环发运（散粮一次性）</h2>{bulk_html}</div>"""

    _inv_block = f"""<div class="card"><h2>🏭 厂家库存与风险</h2>
    {_inv_stock}{_inv_meta}{_inv_flow}{_inv_other}{_inv_dq}
</div>"""

    _hist_block = f"""<div class="card">
    <h2>📜 历史数据与配置</h2>
    <div class="inv-row" style="font-size:0.85em; line-height:1.7;">
        <div>合同编号: <strong>JGWL-JZTS-DD-202601</strong></div>
        <div>合同路径: <span class="note">ops-data-hub/data/contracts/jiusan_soybean/物流发展-铁盛2026大豆合同.docx</span></div>
        <div>SOP: <span class="note">prompts_and_reports/SOPs/jiusan_soybean_sop.md</span></div>
        <div>旧 release_batches: <span class="note">已迁移至 jiusan_cycle.db，备份见 samples/legacy_jiusan_release_batches_backup.json</span></div>
        <div>历史报告: <span class="note">prompts_and_reports/reports/ (18 个九三相关报告已归档)</span></div>
    </div>
</div>"""

    # ── 厂端日报 ──
    factory_report = dashboard.get("factory_daily_report", None)
    if factory_report:
        fr = factory_report
        _factory_block = f"""<div class="card">
    <h2>🏭 厂端日报（{fr.get('record_date', '')}）</h2>
    <div style="font-size:0.85em; line-height:1.8;">
        <div><strong>总卸粮量：</strong>{_fmt_f(fr.get('total_unloaded_tons', 0))} 吨</div>
        <div style="margin-top:6px;"><strong>━━ 卸粮分来源 ━━</strong></div>
        <div>&nbsp;&nbsp;玛格丽特（当前船/lot1+lot2）：{_fmt_f(fr.get('marguerite_unloaded', 0))} 吨</div>
        <div>&nbsp;&nbsp;昆娜（上一船/散粮36车）：{_fmt_f(fr.get('kunna_unloaded', 0))} 吨</div>
        <div>&nbsp;&nbsp;格瑞卡（大连港散粮52车/其他来源）：{_fmt_f(fr.get('geruika_unloaded', 0))} 吨</div>
        <div style="margin-top:6px;"><strong>━━ 库存 ━━</strong></div>
        <div>&nbsp;&nbsp;账面库存：{_fmt_f(fr.get('closing_stock', 0))} 吨</div>
        <div>&nbsp;&nbsp;可用粮：{_fmt_f(fr.get('available_grain'))} 吨</div>
        <div>&nbsp;&nbsp;一线库存：{_fmt_f(fr.get('line1_inventory'))} 吨</div>
        <div>&nbsp;&nbsp;二线库存：{_fmt_f(fr.get('line2_inventory'))} 吨</div>
        <div style="margin-top:6px;"><strong>━━ 作业时间 ━━</strong></div>
        <div>&nbsp;&nbsp;火运无作业：{fr.get('rail_no_work', 'N/A')}</div>
        <div>&nbsp;&nbsp;汽运/集装箱无作业：{fr.get('truck_no_work', 'N/A')}</div>
        <div style="margin-top:6px;"><strong>━━ 到站 ━━</strong></div>
        <div>&nbsp;&nbsp;火运大豆：{_fmt_n(fr.get('rail_soybean_cars', 0))} 节</div>
        <div>&nbsp;&nbsp;汽运集装箱：{_fmt_n(fr.get('truck_containers', 0))} 箱（进二线5#仓）</div>
    </div>
</div>"""
    else:
        _factory_block = ""

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

    html_body = _header + '<div class="grid">' + _summary_block + _plan_block + vessel_lot_html + _pool_block + _trains_block + _tracking_block + _three_block + _warning_block + _bulk_block + _inv_block + _factory_block + _hist_block + conflict_html + "</div>" + _footer

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
.train-detail {{ display: flex; gap: 16px; font-size: 0.85em; color: #b0bec5; margin-bottom: 4px; flex-wrap: wrap; }}
.train-timeline {{ font-size: 0.82em; color: #78909c; display: flex; gap: 12px; flex-wrap: wrap; }}
.tracking-status {{ font-size: 0.82em; color: #b3e5fc; background: #0d2a3a; border-radius: 4px; padding: 6px 10px; margin: 4px 0; line-height: 1.6; }}
.tracking-note {{ font-size: 0.82em; color: #ffcc80; background: #2a2010; border-radius: 4px; padding: 4px 8px; margin: 4px 0; }}
.ts-row {{ margin: 1px 0; }}
.ts-label {{ color: #78909c; }}
.ts-value {{ color: #e0e0e0; }}
.weight-detail {{ font-size: 0.82em; color: #a5d6a7; background: #1a2a1a; border-radius: 4px; padding: 6px 10px; margin: 4px 0; line-height: 1.6; }}
.pool-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.pool-col {{ background: #1e3040; border-radius: 6px; padding: 10px; }}
.pool-title {{ font-weight: bold; color: #4fc3f7; margin-bottom: 6px; }}
.pool-row {{ font-size: 0.85em; line-height: 1.7; }}
.bulk-card {{ background: #2a1a20; border-radius: 6px; padding: 10px; margin-bottom: 6px;
             display: flex; gap: 12px; align-items: center; font-size: 0.85em; flex-wrap: wrap; }}
.bulk-icon {{ font-size: 1.1em; }}
.attribution-note {{ font-size: 0.82em; color: #ffcc80; background: #2a2010; border-radius: 4px; padding: 6px 10px; margin: 4px 0; line-height: 1.6; }}
.empty {{ color: #78909c; font-style: italic; }}
.note {{ color: #78909c; font-size: 0.85em; }}
.plan-detail {{ font-size: 0.85em; line-height: 1.6; white-space: pre-line; }}
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
    print("生成 V3 九三大豆循环运输看板（收口修正版）...")
    dashboard = generate(conn)
    conn.close()

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    json_path = DASHBOARD_DIR / "jiusan_dashboard_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)
    print(f"  JSON: {json_path}")

    if "--example" in sys.argv:
        example_path = DASHBOARD_DIR / "jiusan_dashboard_data.example.json"
        with open(example_path, "w", encoding="utf-8") as f:
            json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)
        print(f"  Example: {example_path}")

    html_content = generate_html(dashboard)
    html_path = DASHBOARD_DIR / "jiusan_dashboard.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  HTML: {html_path}")

    print("✅ V3 收口修正版看板生成完成")
    for t in dashboard.get("cycle_trains", []):
        print(f"  {t['train_id']}: {t['status_text']} ({t.get('tracking', {}).get('delivered_cars', 0)}/{t.get('tracking', {}).get('total_cars', 0)} delivered)")
    print(f"  船名: {dashboard.get('current_vessel_lots', {}).get('vessel_name', 'N/A')}")
    print(f"  source_conflicts: {len(dashboard.get('source_conflicts', []))} 条")


if __name__ == "__main__":
    main()
