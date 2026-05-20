#!/usr/bin/env python3
"""
九三大豆 Phase 2 初始化脚本（含箱型/车型重量规则）

功能：
1. 创建/重建 jiusan_cycle.db（schema 落库）
2. 扫描 95306 新船窗口，计算箱型/车型重量
3. 写入两个集装箱循环列 + round 1 runs（含 container_type_summary）
4. 写入散粮一次性发运事实（含 bulk_wagon_type_summary）
5. 写入发运计划版本样例
6. 写入厂家库存样例（line_in_qty 使用业务重量）
7. 写入 scan log

Usage:
    python3 scripts/jiusan_phase2_init.py [--force]
"""
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── 路径 ────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

# 确保可以 import scripts/
sys.path.insert(0, str(REPO_ROOT))

# ── 引入箱型/车型规则 ──

from scripts.jiusan_container_type_rules import (
    calc_container_train_weight,
    calc_bulk_wagon_weight,
)

# ── 路径 ────────────────────────────────────────────

SCHEMA_PATH = REPO_ROOT / "schema" / "jiusan_cycle_schema.sql"
JIUSAN_DB = REPO_ROOT / "data" / "jiusan_cycle.db"
RAIL95306_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")


# ── 数据库操作 ───────────────────────────────────────

def get_conn(db_path=None):
    if db_path is None:
        db_path = JIUSAN_DB
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn, schema_path=None):
    if schema_path is None:
        schema_path = SCHEMA_PATH
    if not schema_path.exists():
        print(f"[ERROR] Schema not found: {schema_path}", file=sys.stderr)
        sys.exit(1)
    sql = schema_path.read_text()
    conn.executescript(sql)
    conn.commit()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"  Schema loaded: {len(tables)} tables ({', '.join(r['name'] for r in tables)})")


def table_exists(conn, name):
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def clear_table(conn, name):
    if table_exists(conn, name):
        conn.execute(f"DELETE FROM {name}")
        print(f"  Cleared table: {name}")


# ── 95306 扫描 ──────────────────────────────────────

def scan_new_vessel():
    """从 rail95306-sync DB 只读查询新船窗口数据"""
    if not RAIL95306_DB.exists():
        print(f"[ERROR] rail95306 DB not found: {RAIL95306_DB}", file=sys.stderr)
        return None

    conn = sqlite3.connect(str(RAIL95306_DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT ydid, car_no, car_model, transport_mode_name,
               container_no_raw, container_numbers_json,
               ticketed_at, departed_at, arrived_at,
               status_code, status_name, marked_weight,
               freight_fee
        FROM shipments
        WHERE cargo_name = '大豆'
          AND origin_name = '高桥镇'
          AND destination_name = '新台子'
          AND ticketed_at >= '2026-05-19 06:00'
        ORDER BY ticketed_at
    """).fetchall()

    records = [dict(r) for r in rows]
    conn.close()

    # 分类汇总
    container_05_19 = [
        r for r in records
        if r['transport_mode_name'] == '集装箱运输'
        and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-19')
    ]
    container_05_20 = [
        r for r in records
        if r['transport_mode_name'] == '集装箱运输'
        and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-20')
    ]
    bulk_05_19 = [
        r for r in records
        if r['transport_mode_name'] == '整车运输'
        and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-19')
    ]

    # 计算总重量（保留，用于 summary）
    def total_weight(recs):
        total = 0.0
        for r in recs:
            w = r.get('marked_weight')
            if w:
                try:
                    total += float(w)
                except (ValueError, TypeError):
                    pass
        return round(total, 2)

    # ── 箱型/车型统计 ──
    container_0519_stats = calc_container_train_weight(container_05_19)
    container_0520_stats = calc_container_train_weight(container_05_20)
    bulk_stats = calc_bulk_wagon_weight(bulk_05_19)

    result = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_records": len(records),
        "container_05_19": {
            "count": len(container_05_19),
            "total_weight": total_weight(container_05_19),
            "first_ticketed": container_05_19[0]['ticketed_at'] if container_05_19 else None,
            "last_ticketed": container_05_19[-1]['ticketed_at'] if container_05_19 else None,
            "departed_at": container_05_19[0]['departed_at'] if container_05_19 else None,
            "arrived_at": container_05_19[0]['arrived_at'] if container_05_19 else None,
            "status": container_05_19[0]['status_name'] if container_05_19 else None,
            "container_type_summary": container_0519_stats,
        },
        "container_05_20": {
            "count": len(container_05_20),
            "total_weight": total_weight(container_05_20),
            "first_ticketed": container_05_20[0]['ticketed_at'] if container_05_20 else None,
            "last_ticketed": container_05_20[-1]['ticketed_at'] if container_05_20 else None,
            "status": container_05_20[0]['status_name'] if container_05_20 else None,
            "container_type_summary": container_0520_stats,
        },
        "bulk_05_19": {
            "count": len(bulk_05_19),
            "total_weight": total_weight(bulk_05_19),
            "first_ticketed": bulk_05_19[0]['ticketed_at'] if bulk_05_19 else None,
            "last_ticketed": bulk_05_19[-1]['ticketed_at'] if bulk_05_19 else None,
            "departed_at": bulk_05_19[0]['departed_at'] if bulk_05_19 else None,
            "arrived_at": bulk_05_19[0]['arrived_at'] if bulk_05_19 else None,
            "status": bulk_05_19[0]['status_name'] if bulk_05_19 else None,
            "bulk_wagon_type_summary": bulk_stats,
            "known_cars": [
                {"car_no": "8103798", "model": "L18"},
                {"car_no": "8101469", "model": "L18"},
                {"car_no": "8101431", "model": "L18"},
                {"car_no": "8105474", "model": "L70"},
            ],
        },
    }
    return result


# ── 写入 MVP 数据 ───────────────────────────────────

def write_trains_and_runs(conn, scan):
    """写入两个集装箱循环列 + runs"""
    now = datetime.now().isoformat()

    # container_cycle_train_01
    conn.execute("""
        INSERT OR REPLACE INTO jiusan_cycle_trains
        (id, train_type, lot, status, current_round, transport_mode, destination_line, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "container_cycle_train_01", "container", "lot01",
        "returning", 1, "container", "三三〇处专用线", now, now
    ))

    # container_cycle_train_01 round 1 run
    c1 = scan['container_05_19']
    c1_types = json.dumps(c1.get('container_type_summary', {}), ensure_ascii=False)
    conn.execute("""
        INSERT OR REPLACE INTO jiusan_cycle_train_runs
        (id, train_id, round_no, wagon_count, container_count, total_weight,
         depart_time, arrive_time, status, source, source_ref, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"run_{uuid.uuid4().hex[:12]}", "container_cycle_train_01", 1,
        c1['count'], c1['count'] * 2, c1.get('total_weight', 0),
        c1['departed_at'], c1['arrived_at'],
        "unloaded", "95306_sync", f"scan_{scan['scan_time']}",
        f"已交付，返空中，预计今晚到锦州港。"
        f"箱型: {c1_types}",
        now, now
    ))
    print(f"  ✅ container_cycle_train_01: {c1['count']}车 / {c1.get('total_weight', 0)}吨 / 状态=returning")
    c1_summary = c1.get('container_type_summary', {})
    print(f"     敞顶箱{c1_summary.get('open_top_count', 0)}箱×28.5 + 顶开门箱{c1_summary.get('top_open_count', 0)}箱×26.7")
    print(f"     business_weight={c1_summary.get('business_weight_tons', '?')}t / rail_marked={c1_summary.get('rail_marked_weight_tons', '?')}t / diff={c1_summary.get('weight_diff_tons', '?')}t")

    # container_cycle_train_02
    conn.execute("""
        INSERT OR REPLACE INTO jiusan_cycle_trains
        (id, train_type, lot, status, current_round, transport_mode, destination_line, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "container_cycle_train_02", "container", "lot01",
        "loaded", 1, "container", "三三〇处专用线", now, now
    ))

    # container_cycle_train_02 round 1 run
    c2 = scan['container_05_20']
    c2_types = json.dumps(c2.get('container_type_summary', {}), ensure_ascii=False)
    conn.execute("""
        INSERT OR REPLACE INTO jiusan_cycle_train_runs
        (id, train_id, round_no, wagon_count, container_count, total_weight,
         depart_time, status, source, source_ref, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"run_{uuid.uuid4().hex[:12]}", "container_cycle_train_02", 1,
        c2['count'], c2['count'] * 2, c2.get('total_weight', 0),
        None,  # depart_time — 尚未发车
        "pending", "95306_sync", f"scan_{scan['scan_time']}",
        f"锦州港装箱完成，待发。"
        f"箱型: {c2_types}",
        now, now
    ))
    print(f"  ✅ container_cycle_train_02: {c2['count']}车 / {c2.get('total_weight', 0)}吨 / 状态=loaded")
    c2_summary = c2.get('container_type_summary', {})
    print(f"     敞顶箱{c2_summary.get('open_top_count', 0)}箱×28.5 + 顶开门箱{c2_summary.get('top_open_count', 0)}箱×26.7")
    print(f"     business_weight={c2_summary.get('business_weight_tons', '?')}t / rail_marked={c2_summary.get('rail_marked_weight_tons', '?')}t / diff={c2_summary.get('weight_diff_tons', '?')}t")


def write_bulk_dispatch(conn, scan):
    """写入散粮一次性发运事实 — events + flows"""
    now = datetime.now().isoformat()
    b = scan['bulk_05_19']

    # jiusan_resource_events — 散粮一次性发运
    b_stats = json.dumps(b.get('bulk_wagon_type_summary', {}), ensure_ascii=False)
    conn.execute("""
        INSERT INTO jiusan_resource_events
        (id, pool_type, event_type, quantity, event_time, source, source_ref, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"bulk_{uuid.uuid4().hex[:12]}", "wagon", "bulk_dispatch_once",
        b['count'], b['first_ticketed'] or now,
        "95306_sync", f"scan_{scan['scan_time']}",
        f"散粮{b['count']}车一次性发运/入库，用户确认不返回锦州，非循环列。"
        f"车型统计: {b_stats}",
        now
    ))
    print(f"  ✅ bulk_dispatch_once: {b['count']}车 / {b.get('total_weight', 0)}吨 / 一次性发运")
    b_summary = b.get('bulk_wagon_type_summary', {})
    print(f"     L18={b_summary.get('L18_count', 0)}车×60 + L70={b_summary.get('L70_count', 0)}车×69")
    print(f"     business_weight={b_summary.get('business_weight_tons', '?')}t / rail_marked={b_summary.get('rail_marked_weight_tons', '?')}t / diff={b_summary.get('weight_diff_tons', '?')}t")

    # jiusan_flows — 散粮发运/到达流量
    b_summary = b.get('bulk_wagon_type_summary', {})
    flow_fields = {
        "yesterday_dispatched_bulk": b['count'],
        "yesterday_arrived_bulk_count": b['count'],
        "yesterday_arrived_bulk_time": b['arrived_at'],
        "bulk_is_once_off": True,
        "bulk_wagon_type_summary": b_summary,
        "bulk_business_weight_tons": b_summary.get('business_weight_tons', 0),
        "bulk_rail_marked_weight_tons": b_summary.get('rail_marked_weight_tons', 0),
        "bulk_weight_diff_tons": b_summary.get('weight_diff_tons', 0),
        "bulk_weight_source": "bulk_wagon_model_rule",
        "bulk_note": f"散粮{b['count']}车一次性发运，用户确认不返回锦州，非循环列。不计入循环列效率。",
    }
    conn.execute("""
        INSERT INTO jiusan_flows
        (id, flow_date, flow_type, source, source_ref, fields_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        f"flow_{uuid.uuid4().hex[:12]}", "2026-05-19", "daily",
        "95306_calc", f"scan_{scan['scan_time']}", json.dumps(flow_fields, ensure_ascii=False),
        now
    ))
    print(f"  ✅ flow recorded: bulk_is_once_off=true")


def write_plan_sample(conn):
    """写入发运计划版本样例"""
    now = datetime.now().isoformat()

    plan_details = {
        "container": {
            "target_cycle_days": 2,
            "target_train_count": 3,
            "average_daily_train_count": 1.5,
            "frequency_label": "2天3列",
            "target_tons_per_day": 12000,
        },
        "bulk_wagon": {
            "target_cycle_days": 1,
            "target_train_count": 1,
            "average_daily_train_count": 1.0,
            "frequency_label": "按需/暂停",
            "note": "散粮40车已发，本次不参与循环，后续恢复待确认",
        },
        "factory_consumption": 5500,
        "daily_target": 12000,
        "notes": "当前计划B：两列集装箱循环运输中。散粮列暂不恢复。",
    }

    conn.execute("""
        INSERT OR REPLACE INTO jiusan_shipment_plans
        (id, name, is_active, effective_from, effective_to, plan_details_json, superseded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "plan_B", "第二轮调整计划（当前）", 1,
        "2026-05-16", None,
        json.dumps(plan_details, ensure_ascii=False),
        "plan_A", now
    ))
    print(f"  ✅ plan_B active: {json.dumps(plan_details, ensure_ascii=False)}")


def write_inventory_sample(conn, scan):
    """写入厂家库存样例（line_in_qty 使用业务重量）"""
    now = datetime.now().isoformat()

    # 计算已到站业务重量
    c1 = scan.get('container_05_19', {})
    c1_summary = c1.get('container_type_summary', {})
    c1_business = c1_summary.get('business_weight_tons', 0)
    b = scan.get('bulk_05_19', {})
    b_summary = b.get('bulk_wagon_type_summary', {})
    b_business = b_summary.get('business_weight_tons', 0)

    # 05-19 已到达的本线入库业务重量
    arrived_business_weight = c1_business + b_business

    inv = {
        "record_date": "2026-05-20",
        "opening_stock": 42000.0,
        "line_in_qty": round(arrived_business_weight, 1),  # 使用业务重量
        "other_source_in_qty": 0.0,
        "consumption": 5500.0,
        "closing_stock": 45000.0,
        "adjustment": 0.0,
        "red_line": 20000.0,
        "days_supported": 8.2,
    }

    other_reverse = (
        inv['closing_stock'] - inv['opening_stock']
        - inv['line_in_qty'] + inv['consumption']
    )

    conn.execute("""
        INSERT OR REPLACE INTO jiusan_factory_inventory
        (id, record_date, opening_stock, line_in_qty, other_source_in_qty,
         consumption, closing_stock, adjustment, red_line, days_supported,
         source, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"inv_{uuid.uuid4().hex[:12]}",
        inv['record_date'],
        inv['opening_stock'],
        inv['line_in_qty'],
        inv['other_source_in_qty'],
        inv['consumption'],
        inv['closing_stock'],
        inv['adjustment'],
        inv['red_line'],
        inv['days_supported'],
        "manual",
        "Phase 2 weight patch — line_in_qty 使用业务重量。"
        "集装箱05-19业务重量: {:.1f}吨 + 散粮业务重量: {:.1f}吨 = {:.1f}吨。"
        "反推其他来源: {:.0f} 吨。".format(
            c1_business, b_business, arrived_business_weight, other_reverse
        ),
        now
    ))
    print(f"  ✅ inventory sample: 期末库存={inv['closing_stock']}吨 / 红线={inv['red_line']}吨 / 天数={inv['days_supported']}天")


def write_scan_log(conn, scan):
    """写入扫描日志"""
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO jiusan_95306_scan_log
        (id, scan_time, scan_type, new_records, matched_to_lot, matched_to_train, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"scan_{uuid.uuid4().hex[:12]}",
        scan['scan_time'], "manual",
        scan['total_records'],
        scan['container_05_19']['count'] + scan['container_05_20']['count'] + scan['bulk_05_19']['count'],
        2,  # 2 container trains matched
        json.dumps(scan, ensure_ascii=False, default=str),
        now
    ))
    print(f"  ✅ scan log: {scan['total_records']} records")


# ── 主流程 ──────────────────────────────────────────

def main():
    print("=" * 60)
    print("九三大豆 Phase 2 MVP 初始化")
    print("=" * 60)

    force = "--force" in sys.argv

    # Step 1: 扫描 95306
    print("\n[1/6] 扫描 95306 新船窗口...")
    scan = scan_new_vessel()
    if scan is None:
        print("[ERROR] 95306 扫描失败，中止", file=sys.stderr)
        sys.exit(1)
    c1 = scan['container_05_19']
    c2 = scan['container_05_20']
    b = scan['bulk_05_19']
    print(f"  集装箱 05-19: {c1['count']}车 / {c1['total_weight']}吨 / 状态={c1['status']}")
    print(f"  集装箱 05-20: {c2['count']}车 / {c2['total_weight']}吨 / 状态={c2['status']}")
    print(f"  散粮 05-19: {b['count']}车 / {b['total_weight']}吨 / 状态={b['status']}")

    # Step 2: 连接数据库
    print("\n[2/6] 初始化 jiusan_cycle.db...")
    if force:
        if JIUSAN_DB.exists():
            JIUSAN_DB.unlink()
            print(f"  Removed existing DB (--force)")
    conn = get_conn()
    ensure_schema(conn)

    # 清空存量数据（避免重复写入冲突）
    for tbl in ['jiusan_cycle_train_runs', 'jiusan_cycle_trains',
                'jiusan_resource_events', 'jiusan_flows',
                'jiusan_adjustments', 'jiusan_shipment_plans',
                'jiusan_factory_inventory', 'jiusan_snapshots',
                'jiusan_95306_scan_log']:
        clear_table(conn, tbl)

    # Step 3: 写入循环列 + runs
    print("\n[3/6] 写入集装箱循环列...")
    write_trains_and_runs(conn, scan)

    # Step 4: 写入散粮
    print("\n[4/6] 写入散粮一次性发运事实...")
    write_bulk_dispatch(conn, scan)

    # Step 5: 写入计划 + 库存
    print("\n[5/6] 写入发运计划 + 厂家库存...")
    write_plan_sample(conn)
    write_inventory_sample(conn, scan)

    # Step 6: 写入 scan log
    print("\n[6/6] 写入扫描日志...")
    write_scan_log(conn, scan)

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("Phase 2 MVP 初始化完成")
    print(f"  DB: {JIUSAN_DB}")
    print("=" * 60)


def verify():
    """验证写入数据"""
    conn = get_conn()

    trains = conn.execute("SELECT id, train_type, status, current_round FROM jiusan_cycle_trains").fetchall()
    print(f"\n循环列: {len(trains)} 列")
    for t in trains:
        print(f"  {t['id']}: type={t['train_type']}, status={t['status']}, round={t['current_round']}")

    runs = conn.execute("SELECT train_id, round_no, wagon_count, container_count, status FROM jiusan_cycle_train_runs").fetchall()
    print(f"运行记录: {len(runs)} 条")
    for r in runs:
        print(f"  {r['train_id']} round={r['round_no']}: {r['wagon_count']}车/{r['container_count']}箱 status={r['status']}")

    events = conn.execute("SELECT event_type, pool_type, quantity, source FROM jiusan_resource_events").fetchall()
    print(f"资源事件: {len(events)} 条")
    for e in events:
        print(f"  {e['event_type']}: {e['pool_type']} × {e['quantity']} ({e['source']})")

    flows = conn.execute("SELECT flow_date, source FROM jiusan_flows").fetchall()
    print(f"流量记录: {len(flows)} 条")

    plans = conn.execute("SELECT id, is_active, effective_from FROM jiusan_shipment_plans").fetchall()
    print(f"计划版本: {len(plans)} 条")
    for p in plans:
        print(f"  {p['id']}: active={p['is_active']}, from={p['effective_from']}")

    inv = conn.execute("SELECT record_date, closing_stock, red_line FROM jiusan_factory_inventory").fetchall()
    print(f"库存记录: {len(inv)} 条")
    for i in inv:
        print(f"  {i['record_date']}: clos={i['closing_stock']}, red={i['red_line']}")

    scans = conn.execute("SELECT scan_time, scan_type, new_records FROM jiusan_95306_scan_log").fetchall()
    print(f"扫描日志: {len(scans)} 条")

    conn.close()


if __name__ == "__main__":
    main()
    verify()
