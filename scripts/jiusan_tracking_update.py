#!/usr/bin/env python3
"""
九三大豆 Phase 2.5-B — 基于 95306 shipments 表的状态追踪

读取 95306 本地数据库 shipments 表，为指定循环列生成 tracking_status。

注意：
1. 当前车辆轨迹跟踪（vehicle_tracking / shipment_tracking_routes）数据尚未被填充。
   95306 跟踪 API（query_tracking）存在 Python 3.9 兼容问题，尚未接入定时同步。
2. 本脚本基于 shipments 表的状态字段（status_code / latest_stage / latest_event_time），
   只能提供 shipment 级别状态，无法提供车辆级别位置轨迹。
3. 如需完整车辆轨迹跟踪，需升级运行环境至 Python 3.10+ 并启用 query_tracking 工具。

Usage:
    python3 scripts/jiusan_tracking_update.py
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIL95306_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
REPO_ROOT = Path(__file__).resolve().parent.parent
JIUSAN_DB = REPO_ROOT / "data" / "jiusan_cycle.db"

STATUS_MAP = {
    "30": "已装车",
    "35": "已制单",
    "40": "已发车（在途）",
    "60": "已到达",
    "80": "货物已交付",
}

DETAIL_FIELDS = [
    "ydid", "car_no", "status_code", "status_name",
    "latest_stage_key", "latest_stage_name", "latest_event_time",
    "departed_at", "arrived_at", "delivered_at",
]


def get_jiusan_shipments(conn) -> dict:
    """从 95306 DB 查询九三大豆的所有 shipments，按日期分组"""
    rows = conn.execute("""
        SELECT ydid, car_no, status_code, status_name,
               latest_stage_key, latest_stage_name, latest_event_time,
               departed_at, arrived_at, delivered_at,
               ticketed_at, transport_mode_name,
               origin_name, destination_name, freight_fee
        FROM shipments
        WHERE cargo_name = '大豆'
          AND origin_name = '高桥镇'
          AND destination_name = '新台子'
          AND ticketed_at >= '2026-05-19'
        ORDER BY ticketed_at
    """).fetchall()

    records = [dict(r) for r in rows]

    # 分组
    container_05_19 = [r for r in records
                       if r['transport_mode_name'] == '集装箱运输'
                       and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-19')]
    container_05_20 = [r for r in records
                       if r['transport_mode_name'] == '集装箱运输'
                       and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-20')]
    bulk_05_19 = [r for r in records
                  if r['transport_mode_name'] == '整车运输'
                  and r['ticketed_at'] and r['ticketed_at'].startswith('2026-05-19')]

    return {
        "container_05_19": container_05_19,
        "container_05_20": container_05_20,
        "bulk_05_19": bulk_05_19,
    }


def build_tracking_status(records: list[dict], train_label: str) -> dict:
    """为一列集装箱生成 tracking_status"""
    now = datetime.now(timezone.utc).isoformat()

    total = len(records)

    # 按状态码分组
    status_groups = {}
    for r in records:
        sc = r.get('status_code', '')
        status_groups.setdefault(sc, []).append(r)

    # 汇总统计
    departed_count = sum(len(v) for k, v in status_groups.items() if int(k or 0) >= 40)
    arrived_count = sum(len(v) for k, v in status_groups.items() if int(k or 0) >= 60)
    delivered_count = sum(len(v) for k, v in status_groups.items() if int(k or 0) >= 80)
    unknown_count = sum(len(v) for k, v in status_groups.items() if k not in STATUS_MAP)

    # 确定整体 summary_status
    if all(int(r.get('status_code', 0)) >= 80 for r in records):
        if train_label == "container_cycle_train_01":
            summary_status = "已交付；返空待人工确认"
        else:
            summary_status = "已交付"
    elif all(int(r.get('status_code', 0)) >= 60 for r in records):
        summary_status = "已到达"
    elif any(int(r.get('status_code', 0)) >= 40 for r in records):
        summary_status = "已发车/在途"
    elif all(int(r.get('status_code', 0)) == 35 for r in records):
        summary_status = "已制单/待发"
    else:
        summary_status = "状态未知"

    # 最新事件时间
    latest_times = [r.get('latest_event_time', '') for r in records if r.get('latest_event_time')]
    latest_event_time = max(latest_times) if latest_times else None

    # 取首个非空 latest_stage_name
    latest_stage = None
    for r in records:
        if r.get('latest_stage_name'):
            latest_stage = r['latest_stage_name']
            break
    if not latest_stage:
        for r in records:
            if r.get('status_name'):
                latest_stage = r['status_name']
                break

    # 样本车辆（最多 3 个）
    sample_cars = []
    seen_codes = set()
    for r in records:
        code = r.get('status_code', '')
        if code not in seen_codes:
            seen_codes.add(code)
            sample_cars.append({
                "car_no": r.get('car_no', ''),
                "status": r.get('status_name', ''),
                "latest_stage_name": r.get('latest_stage_name', ''),
                "latest_event_time": r.get('latest_event_time', ''),
                "departed_at": r.get('departed_at', ''),
                "arrived_at": r.get('arrived_at', ''),
            })
        if len(sample_cars) >= 3:
            break

    return {
        "enabled": True,
        "source": "rail95306-sync (shipments table)",
        "last_checked_at": now,
        "summary_status": summary_status,
        "current_location": latest_stage,
        "latest_event_time": latest_event_time,
        "tracked_car_count": total,
        "departed_car_count": departed_count,
        "arrived_car_count": arrived_count,
        "delivered_car_count": delivered_count,
        "unknown_car_count": unknown_count,
        "sample_cars": sample_cars,
        "note": "基于 shipments 表状态字段。车辆级实时轨迹（vehicle_tracking表）尚未填充，95306 跟踪 API 需 Python 3.10+。",
    }


def main():
    print("=" * 60)
    print("九三大豆 Phase 2.5-B — 95306 追踪状态更新")
    print("=" * 60)

    # 连接 95306 DB
    if not RAIL95306_DB.exists():
        print(f"[ERROR] 95306 DB not found: {RAIL95306_DB}", file=sys.stderr)
        sys.exit(1)

    src_conn = sqlite3.connect(str(RAIL95306_DB))
    src_conn.row_factory = sqlite3.Row

    # 获取九三 shipments
    shipment_groups = get_jiusan_shipments(src_conn)
    src_conn.close()

    c1 = shipment_groups['container_05_19']
    c2 = shipment_groups['container_05_20']
    b = shipment_groups['bulk_05_19']

    print(f"\n  集装箱05-19: {len(c1)}车")
    print(f"  集装箱05-20: {len(c2)}车")
    print(f"  散粮05-19: {len(b)}车")

    # 生成 tracking status
    track_01 = build_tracking_status(c1, "container_cycle_train_01")
    track_02 = build_tracking_status(c2, "container_cycle_train_02")

    print(f"\n  container_cycle_train_01:")
    print(f"    状态: {track_01['summary_status']}")
    print(f"    最新事件: {track_01['latest_event_time']}")
    print(f"    已交付: {track_01['delivered_car_count']}/{track_01['tracked_car_count']}")

    print(f"\n  container_cycle_train_02:")
    print(f"    状态: {track_02['summary_status']}")
    print(f"    最新事件: {track_02['latest_event_time']}")
    print(f"    已发车: {track_02['departed_car_count']}/{track_02['tracked_car_count']}")

    # 写入 jiusan_cycle.db 的 scan log details_json
    if JIUSAN_DB.exists():
        jiusan_conn = sqlite3.connect(str(JIUSAN_DB))
        jiusan_conn.row_factory = sqlite3.Row
        now_iso = datetime.now().isoformat()
        tracking_snapshot = {
            "container_cycle_train_01": track_01,
            "container_cycle_train_02": track_02,
        }
        jiusan_conn.execute("""
            INSERT INTO jiusan_95306_scan_log
            (id, scan_time, scan_type, new_records, matched_to_lot, matched_to_train, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"tracking_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            now_iso, "tracking_poll",
            len(c1) + len(c2) + len(b),
            len(c1) + len(c2) + len(b),
            2,
            json.dumps(tracking_snapshot, ensure_ascii=False, default=str),
            now_iso
        ))
        jiusan_conn.commit()
        jiusan_conn.close()
        print(f"\n  ✅ 追踪快照写入 jiusan_cycle.db")

    # 输出 JSON 格式（供 board generator 使用）
    tracking_result = {
        "last_updated": datetime.now().isoformat(),
        "container_cycle_train_01": track_01,
        "container_cycle_train_02": track_02,
    }

    # 保存为 sample
    sample_path = REPO_ROOT / "samples" / "jiusan_tracking_status_snapshot.json"
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(tracking_result, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✅ 追踪快照保存: {sample_path}")

    print("\n" + "=" * 60)
    print("追踪状态更新完成")
    print("=" * 60)

    return tracking_result


if __name__ == "__main__":
    main()
