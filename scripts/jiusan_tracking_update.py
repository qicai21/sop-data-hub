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
from sop_hub.utils.time import now_iso_beijing
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIL95306_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
REPO_ROOT = Path(__file__).resolve().parent.parent
JIUSAN_DB = REPO_ROOT / "data" / "jiusan_cycle.db"
TRAIN_IDENTITY_MAP_PATH = REPO_ROOT / "samples" / "jiusan_cycle_train_identity_map.json"

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


def get_jiusan_shipments(conn) -> list[dict]:
    """从 95306 DB 查询九三大豆的所有 shipments，返回原始记录列表。"""
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
          AND transport_mode_name = '集装箱运输'
          AND ticketed_at >= '2026-05-19'
        ORDER BY ticketed_at, departed_at, arrived_at, car_no
    """).fetchall()

    return [dict(r) for r in rows]


def build_tracking_status(records: list[dict], train_label: str) -> dict:
    """为一列集装箱生成 tracking_status"""
    now = now_iso_beijing()

    if not records:
        return {
            "enabled": True,
            "source": "rail95306-sync (shipments table)",
            "last_checked_at": now,
            "summary_status": "待同步",
            "current_location": "",
            "latest_event_time": "",
            "tracked_car_count": 0,
            "departed_car_count": 0,
            "arrived_car_count": 0,
            "delivered_car_count": 0,
            "unknown_car_count": 0,
            "sample_cars": [],
            "note": "暂无匹配的 95306 记录。",
        }

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


def _load_train_identity_map() -> dict:
    """读取最小身份映射 JSON；缺失时回退到内置默认值。"""
    fallback = {
        "_schema_version": "1.0",
        "_source_filters": {
            "cargo_name": "大豆",
            "origin_name": "高桥镇",
            "destination_name": "新台子",
            "transport_mode_name": "集装箱运输",
        },
        "container_cycle_train_01": {"label": "列1Z1", "departed_at": "2026-05-19 11:31:00", "arrived_at": "2026-05-19 19:15:00"},
        "container_cycle_train_02": {"label": "列2Z1", "departed_at": "2026-05-20 15:42:00", "arrived_at": "2026-05-20 20:45:00"},
        "container_cycle_train_03": {"label": "列3Z1", "departed_at": "2026-05-21 20:47:00", "arrived_at": "2026-05-22 04:46:00"},
        "container_cycle_train_04": {"label": "列1Z2?", "departed_at": "2026-05-21 16:15:00", "arrived_at": "2026-05-21 23:45:00", "confidence": "tentative"},
    }
    if TRAIN_IDENTITY_MAP_PATH.exists():
        try:
            loaded = json.loads(TRAIN_IDENTITY_MAP_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
        except (OSError, json.JSONDecodeError):
            pass
    return fallback


def _iter_train_specs(identity_map: dict):
    for train_id, spec in identity_map.items():
        if train_id.startswith("_"):
            continue
        if isinstance(spec, dict):
            yield train_id, spec


def update_tracking_status_table(shipment_groups: dict, identity_map: dict):
    """将 95306 shipment 数据写入 jiusan_tracking_status 表（覆盖刷新）"""
    if not JIUSAN_DB.exists():
        print(f"  [WARN] jiusan_cycle.db 不存在，跳过 tracking_status 写入")
        return
    conn = sqlite3.connect(str(JIUSAN_DB))
    conn.row_factory = sqlite3.Row
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for train_id, spec in _iter_train_specs(identity_map):
        records = shipment_groups.get(train_id, [])
        label = spec.get("label", train_id)

        # 删除该列旧记录
        conn.execute("DELETE FROM jiusan_tracking_status WHERE train_id = ?", (train_id,))
        print(f"  删除 {train_id} 旧记录（{label}，{len(records)} 条）")

        if not records:
            print(f"  [WARN] {train_id} 暂无 95306 记录，跳过写入")
            continue

        # 插入新记录
        for r in records:
            sc = r.get("status_code", "")
            is_on_way = 1 if sc and int(sc) < 60 else 0
            latest_stage = r.get("latest_stage_name", "")
            if not latest_stage:
                latest_stage = r.get("status_name", "")

            conn.execute("""
                INSERT INTO jiusan_tracking_status
                    (id, car_no, ydid, train_id, status_code, status_name,
                     latest_event, latest_event_time, current_node,
                     arrived_at, departed_at, delivered_at,
                     is_on_way, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"ts_{train_id}_{r.get('ydid') or r['car_no']}",
                r["car_no"],
                r.get("ydid", ""),
                train_id,
                sc,
                r.get("status_name", ""),
                latest_stage,
                r.get("latest_event_time", ""),
                latest_stage,  # current_node ≈ latest_stage_name
                r.get("arrived_at", ""),
                r.get("departed_at", ""),
                r.get("delivered_at", ""),
                is_on_way,
                now,
            ))

        # 汇总打印
        cur = conn.execute(
            "SELECT status_code, COUNT(*) as cnt FROM jiusan_tracking_status WHERE train_id = ? GROUP BY status_code",
            (train_id,),
        )
        status_summary = {r["status_code"]: r["cnt"] for r in cur.fetchall()}
        print(f"  写入 {train_id} 完成：{status_summary}")

    conn.commit()
    conn.close()
    print("  ✅ tracking_status 表已刷新")


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

    # 获取身份映射与 95306 shipments
    identity_map = _load_train_identity_map()
    shipment_records = get_jiusan_shipments(src_conn)
    src_conn.close()

    shipment_groups = {}
    for train_id, spec in _iter_train_specs(identity_map):
        departed_at = spec.get("departed_at", "")
        arrived_at = spec.get("arrived_at", "")
        ticketed_date = spec.get("ticketed_date", "")
        matched = []
        for r in shipment_records:
            if departed_at and r.get("departed_at") != departed_at:
                continue
            if arrived_at and r.get("arrived_at") != arrived_at:
                continue
            if ticketed_date and not (r.get("ticketed_at") or "").startswith(ticketed_date):
                continue
            matched.append(r)
        shipment_groups[train_id] = matched

    for train_id, spec in _iter_train_specs(identity_map):
        label = spec.get("label", train_id)
        print(f"\n  {label}: {len(shipment_groups.get(train_id, []))}车")

    # 生成 tracking status
    tracking_snapshot = {}
    for train_id, spec in _iter_train_specs(identity_map):
        tracking_snapshot[train_id] = build_tracking_status(shipment_groups.get(train_id, []), train_id)

    # 刷新 jiusan_tracking_status 表
    print()
    print("—" * 60)
    print("刷新 jiusan_tracking_status 表…")
    update_tracking_status_table(shipment_groups, identity_map)

    for train_id, spec in _iter_train_specs(identity_map):
        track = tracking_snapshot[train_id]
        label = spec.get("label", train_id)
        print(f"\n  {train_id} ({label}):")
        print(f"    状态: {track['summary_status']}")
        print(f"    最新事件: {track['latest_event_time']}")
        print(f"    已发车/到站/交付: {track['departed_car_count']}/{track['arrived_car_count']}/{track['delivered_car_count']}")

    # 写入 jiusan_cycle.db 的 scan log details_json
    if JIUSAN_DB.exists():
        jiusan_conn = sqlite3.connect(str(JIUSAN_DB))
        jiusan_conn.row_factory = sqlite3.Row
        now_iso = datetime.now().isoformat()
        jiusan_conn.execute("""
            INSERT INTO jiusan_95306_scan_log
            (id, scan_time, scan_type, new_records, matched_to_lot, matched_to_train, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"tracking_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            now_iso, "tracking_poll",
            sum(len(v) for v in shipment_groups.values()),
            sum(len(v) for v in shipment_groups.values()),
            len(tracking_snapshot),
            json.dumps(tracking_snapshot, ensure_ascii=False, default=str),
            now_iso
        ))
        jiusan_conn.commit()
        jiusan_conn.close()
        print(f"\n  ✅ 追踪快照写入 jiusan_cycle.db")

    # 输出 JSON 格式（供 board generator 使用）
    tracking_result = {
        "last_updated": datetime.now().isoformat(),
        **tracking_snapshot,
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
