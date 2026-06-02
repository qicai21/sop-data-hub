#!/usr/bin/env python3
"""
九三大豆 Phase 2.5 — 录入人工实况快照到 jiusan_cycle.db

从现场数据更新：
  1. 快照 (jiusan_snapshots)
  2. 流量记录 (jiusan_flows)
  3. 厂家库存 (jiusan_factory_inventory)
  4. 循环列状态更新 (jiusan_cycle_trains + jiusan_cycle_train_runs)
"""
from sop_hub.utils.time import now_iso_beijing
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JIUSAN_DB = REPO_ROOT / "data" / "jiusan_cycle.db"

now = now_iso_beijing()
date_str = "2026-05-20"


def get_conn():
    conn = sqlite3.connect(str(JIUSAN_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def main():
    conn = get_conn()
    print("=" * 60)
    print("九三大豆 Phase 2.5 — 人工实况录入")
    print("=" * 60)

    # ── 1. 写入 Snapshot ──
    print("\n[1/4] 写入现场快照...")
    snapshot_fields = {
        "port": {
            "empty_containers": 90,
            "loaded_containers": 71,
            "loaded_container_train_lines": 0,
        },
        "in_transit": {
            "loaded_containers": 108,
            "returning_empty_containers": 104,
            "container_train_01_status": "20日20时到锦州港",
            "container_train_02_status": "已发车",
        },
        "destination": {
            "xintaizi_loaded_containers": 0,
            "xintaizi_empty_containers": 0,
            "line_330_loaded_containers": 4,
            "line_330_empty_containers": 114,
        },
        "_notes": "2026-05-20 现场人工实况，由用户填报",
    }
    conn.execute("""
        INSERT INTO jiusan_snapshots
        (id, snapshot_date, snapshot_time, source, source_ref, fields_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        f"snap_{uuid.uuid4().hex[:12]}",
        date_str, "2026-05-20 07:00",
        "morning_report", "user_field_snapshot_20260520",
        json.dumps(snapshot_fields, ensure_ascii=False),
        now
    ))
    print(f"  ✅ Snapshot written: port.空箱=90, port.重箱=71, 在途重箱=108")

    # ── 2. 写入 Flow ──
    print("\n[2/4] 写入期间流量...")
    flow_fields = {
        "yesterday_loaded_containers": 120,
        "yesterday_dispatched_containers": 0,
        "yesterday_dispatched_bulk": 40,
        "yesterday_arrived_containers": 54,
        "yesterday_arrived_container_cars": 54,
        "yesterday_unloaded_containers": 108,
        "yesterday_returned_empty": 0,
        "yesterday_arrived_bulk_count": 0,
        "bulk_is_once_off": True,
        "_notes": "2026-05-20 现场实况流量。昨日发出40为散粮车（一次性发运已计入此前），昨日到达54车为集装箱列1。",
    }
    conn.execute("""
        INSERT INTO jiusan_flows
        (id, flow_date, flow_type, source, source_ref, fields_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        f"flow_{uuid.uuid4().hex[:12]}",
        date_str, "daily",
        "morning_report", "user_field_snapshot_20260520",
        json.dumps(flow_fields, ensure_ascii=False),
        now
    ))
    print(f"  ✅ Flow written: 装箱=120, 到站=54车, 卸空=108, 散粮发出=40")

    # ── 3. 写入厂家库存（真实值） ──
    print("\n[3/4] 写入厂家库存（真实值）...")
    closing_stock = 5000.0
    opening_stock = 42000.0  # 用户未提供期初，保持之前样本值
    consumption = 0.0  # 用户说日耗=0
    line_in_qty = 5638.2  # 之前的业务重量计算值
    other_source = 0.0
    red_line = 8000.0
    adjustment = 0.0

    # 如果本期没有消耗，库存只增不减
    # 期末 5000 是当前实际值
    # 反推其他来源调整
    other_reverse = closing_stock - opening_stock - line_in_qty + consumption - adjustment
    days_supported = round(closing_stock / 5500, 1) if closing_stock else 0  # 按计划日耗5500算

    conn.execute("""
        INSERT OR REPLACE INTO jiusan_factory_inventory
        (id, record_date, opening_stock, line_in_qty, other_source_in_qty,
         consumption, closing_stock, adjustment, red_line, days_supported,
         source, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"inv_{uuid.uuid4().hex[:12]}",
        date_str,
        opening_stock,
        line_in_qty,
        other_source,
        consumption,
        closing_stock,
        adjustment,
        red_line,
        days_supported,
        "manual",
        "data_quality=user_reported。2026-05-20 用户填报现场库存。"
        f"期末={closing_stock}吨，红线={red_line}吨，日耗={consumption}（停工/待料）。"
        f"反推其他来源: {other_reverse:.0f} 吨。",
        now
    ))
    print(f"  ✅ Inventory: 期末库存={closing_stock}吨 / 红线={red_line}吨 / 数据质量=user_reported")
    print(f"     days_supported={days_supported}")

    # ── 4. 更新循环列状态 ──
    print("\n[4/4] 更新循环列状态...")

    # Train 01: 返空已到→updating return_time, 保留原有箱型统计
    existing_note_01 = conn.execute(
        "SELECT notes FROM jiusan_cycle_train_runs WHERE train_id = 'container_cycle_train_01' AND round_no = 1"
    ).fetchone()
    old_notes_01 = (existing_note_01['notes'] or '') if existing_note_01 else ''
    # 提取箱型 JSON 部分保留
    import re
    type_match_01 = re.search(r'(箱型: \{.+?\})', old_notes_01)
    type_suffix_01 = '。' + type_match_01.group(1) if type_match_01 else ''
    new_notes_01 = (
        "已交付，返空已到锦州港（用户确认 20日20时到港）。95306 状态=80(货物已交付)。"
        + type_suffix_01
    )
    conn.execute("""
        UPDATE jiusan_cycle_train_runs
        SET return_time = ?, notes = ?, updated_at = ?
        WHERE train_id = 'container_cycle_train_01' AND round_no = 1
    """, (
        "2026-05-20 20:00",
        new_notes_01,
        now
    ))
    # Also update train status
    conn.execute("""
        UPDATE jiusan_cycle_trains
        SET status = 'returned', updated_at = ?
        WHERE id = 'container_cycle_train_01'
    """, (now,))
    print(f"  ✅ container_cycle_train_01: 返空已到锦州港 (20:00) → status=returned")

    # Train 02: 已发车→updating depart_time
    # Train 02: 已发车→updating depart_time, 保留原有箱型统计
    existing_note_02 = conn.execute(
        "SELECT notes FROM jiusan_cycle_train_runs WHERE train_id = 'container_cycle_train_02' AND round_no = 1"
    ).fetchone()
    old_notes_02 = (existing_note_02['notes'] or '') if existing_note_02 else ''
    type_match_02 = re.search(r'(箱型: \{.+?\})', old_notes_02)
    type_suffix_02 = '。' + type_match_02.group(1) if type_match_02 else ''
    new_notes_02 = (
        "锦州港已发车（用户确认）。95306 状态=35(已制单)，发车时间待确认。"
        + type_suffix_02
    )
    conn.execute("""UPDATE jiusan_cycle_train_runs SET depart_time = ?, status = 'departed', notes = ?, updated_at = ? WHERE train_id = 'container_cycle_train_02' AND round_no = 1""", (
        None, new_notes_02, now
    ))
    conn.execute("""UPDATE jiusan_cycle_trains SET status = 'departed', updated_at = ? WHERE id = 'container_cycle_train_02'""", (now,))
    print(f"  ✅ container_cycle_train_02: 已发车（用户确认）→ status=departed")

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("人工实况录入完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
