#!/usr/bin/env python3
"""
95306 大豆发运数据只读分析 & lot/列分类验证 (Phase 1)

Usage:
    python3 scripts/jiusan_95306_analysis.py

Output: data/jiusan_95306_analysis.json
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

RAIL95306_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "samples" / "jiusan_95306_analysis.json"


def query_95306() -> list[dict]:
    """只读查询 rail95306-sync DB 中大豆→新台子的发运记录"""
    if not RAIL95306_DB.exists():
        print(f"[ERROR] 95306 database not found: {RAIL95306_DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(RAIL95306_DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT ydid, car_no, car_model, cargo_name,
               transport_mode_code, transport_mode_name,
               origin_name, destination_name,
               container_no_raw, container_numbers_json,
               ticketed_at, departed_at, arrived_at,
               status_code, status_name,
               marked_weight, freight_fee
        FROM shipments
        WHERE cargo_name = '大豆'
          AND origin_name = '高桥镇'
          AND destination_name = '新台子'
        ORDER BY ticketed_at
    """).fetchall()

    result = [dict(r) for r in rows]
    conn.close()
    return result


def classify_lot(ship: dict) -> str:
    """根据设计文档的 lot 分类规则验证：
    - 有箱号 / 集装箱运输 → lot01
    - 散粮车 / K车 / 整车运输 → lot02
    """
    mode = (ship.get("transport_mode_name") or "").strip()
    car_model = (ship.get("car_model") or "").strip()
    container_raw = (ship.get("container_no_raw") or "").strip()
    container_json = (ship.get("container_numbers_json") or "").strip()

    # 规则1: 有箱号 → lot01
    has_container = bool(container_raw) or (container_json and container_json != "[]" and container_json != "null")
    if has_container:
        return "lot01"

    # 规则2: 集装箱运输方式 → lot01
    if "集装箱" in mode:
        return "lot01"

    # 规则3: 散粮车/K车/整车 → lot02
    if "整车" in mode or "散粮" in mode or ("K" in car_model.upper()):
        return "lot02"
    if "敞车" in car_model or "C" in car_model.upper():
        return "lot02"

    # 默认: 按运输方式判断
    if "集装箱" in mode:
        return "lot01"
    return "lot02"


def group_cycle_train_candidates(records: list[dict]) -> list[dict]:
    """按时间窗口 + 运输方式分组，生成循环列候选

    分组逻辑：
    - 同一天/相邻半天发车的同一运输方式视为同一列候选
    - 优先使用 ticketed_at，其次 departed_at，其次 accepted_at
    - 时间窗口阈值：8小时（放宽，因为数据可能跨天）
    """
    def _get_time(r):
        """取最早有效时间"""
        for key in ("ticketed_at", "departed_at", "arrived_at"):
            v = r.get(key)
            if v:
                return v
        return None

    # 只取有时间戳的记录
    timed_records = []
    no_time = 0
    for r in records:
        t = _get_time(r)
        if t:
            r["_sort_time"] = t
            timed_records.append(r)
        else:
            no_time += 1

    print(f"  有时间戳: {len(timed_records)}, 无时间戳: {no_time}")
    timed_records.sort(key=lambda r: r["_sort_time"])

    candidates = []
    current_group = None
    group_idx = 0

    WINDOW_HOURS = 8

    for r in timed_records:
        ts = r["_sort_time"]
        try:
            # 支持 "2026-04-29T09:27:25" 和 "2026-04-29 09:27:25" 两种格式
            ts_clean = ts[:19].replace("T", " ")
            dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            continue

        mode = r.get("transport_mode_name", "")
        lot = classify_lot(r)

        if current_group is None:
            current_group = {
                "group_id": f"train_candidate_{group_idx}",
                "transport_mode": mode,
                "lot": lot,
                "start_time": ts,
                "end_time": ts,
                "wagon_count": 1,
                "ydids": [r["ydid"]],
                "car_nos_set": set(),
                "first_ticketed": dt,
            }
            if r.get("car_no"):
                current_group["car_nos_set"].add(r["car_no"])
            continue

        gap_hours = (dt - current_group["first_ticketed"]).total_seconds() / 3600

        # 同一运输方式 + 8小时内 → 同组
        def _is_container(m):
            return "集装箱" in m

        same_mode = (_is_container(mode) == _is_container(current_group["transport_mode"]))
        if same_mode and gap_hours <= WINDOW_HOURS:
            current_group["end_time"] = ts
            current_group["wagon_count"] += 1
            current_group["ydids"].append(r["ydid"])
            if r.get("car_no"):
                current_group["car_nos_set"].add(r["car_no"])
        else:
            # 保存当前组
            current_group["car_nos"] = list(current_group["car_nos_set"])
            current_group["car_no_count"] = len(current_group["car_nos_set"])
            current_group.pop("car_nos_set", None)
            current_group.pop("first_ticketed", None)
            candidates.append(current_group)

            # 新建组
            group_idx += 1
            current_group = {
                "group_id": f"train_candidate_{group_idx}",
                "transport_mode": mode,
                "lot": lot,
                "start_time": ts,
                "end_time": ts,
                "wagon_count": 1,
                "ydids": [r["ydid"]],
                "car_nos_set": set(),
                "first_ticketed": dt,
            }
            if r.get("car_no"):
                current_group["car_nos_set"].add(r["car_no"])

    # 保存最后一组
    if current_group:
        current_group["car_nos"] = list(current_group["car_nos_set"])
        current_group["car_no_count"] = len(current_group["car_nos_set"])
        current_group.pop("car_nos_set", None)
        current_group.pop("first_ticketed", None)
        candidates.append(current_group)

    return candidates


def analyze(records: list[dict]) -> dict:
    """多维度分析"""
    total = len(records)
    unique_cars = len(set(r.get("car_no") for r in records if r.get("car_no")))

    # 运输方式分布
    mode_counter = Counter()
    for r in records:
        mode_counter[r.get("transport_mode_name", "(unknown)")] += 1

    # 箱号覆盖率
    has_container_raw = sum(1 for r in records if r.get("container_no_raw"))
    has_container_json = sum(1 for r in records if r.get("container_numbers_json") and r["container_numbers_json"] not in ("[]", "null"))
    has_container_any = sum(1 for r in records if (r.get("container_no_raw") or
                                                    (r.get("container_numbers_json") and r["container_numbers_json"] not in ("[]", "null"))))

    # 状态分布
    status_counter = Counter()
    for r in records:
        status_counter[r.get("status_code", "(none)") + " " + r.get("status_name", "")] += 1

    # 时间分布
    daily = defaultdict(int)
    monthly = defaultdict(int)
    for r in records:
        ts = r.get("ticketed_at") or r.get("departed_at") or ""
        if ts:
            day = ts[:10]
            month = ts[:7]
            daily[day] += 1
            monthly[month] += 1

    # 车型分布
    car_model_counter = Counter()
    for r in records:
        car_model_counter[r.get("car_model", "(none)")] += 1

    # 车号连续性检查（按 ticketed_at 排序的车号序列）
    car_nos_ordered = []
    for r in sorted(records, key=lambda x: x.get("ticketed_at") or ""):
        cn = r.get("car_no")
        if cn:
            car_nos_ordered.append(cn)

    # lot 分类验证
    lot_results = []
    for r in records:
        lot = classify_lot(r)
        lot_results.append({
            "ydid": r["ydid"],
            "car_no": r.get("car_no"),
            "transport_mode": r.get("transport_mode_name"),
            "car_model": r.get("car_model"),
            "has_container_raw": bool(r.get("container_no_raw")),
            "has_container_json": bool(r.get("container_numbers_json") and r["container_numbers_json"] not in ("[]", "null")),
            "lot": lot,
        })

    lot_distribution = Counter(l["lot"] for l in lot_results)

    # 循环列候选分组验证
    train_candidates = group_cycle_train_candidates(records)

    return {
        "total_records": total,
        "unique_car_nos": unique_cars,
        "transport_mode_distribution": dict(mode_counter),
        "container_coverage": {
            "has_container_no_raw": has_container_raw,
            "has_container_numbers_json": has_container_json,
            "has_container_any": has_container_any,
            "container_coverage_pct": round(has_container_any / total * 100, 2) if total else 0,
        },
        "status_distribution": dict(status_counter),
        "daily_distribution": {k: v for k, v in sorted(daily.items())},
        "monthly_distribution": dict(sorted(monthly.items())),
        "car_model_distribution": dict(car_model_counter),
        "lot_classification": {
            "lot01_count": lot_distribution.get("lot01", 0),
            "lot02_count": lot_distribution.get("lot02", 0),
            "lot_distribution_pct": {
                "lot01": round(lot_distribution.get("lot01", 0) / total * 100, 2) if total else 0,
                "lot02": round(lot_distribution.get("lot02", 0) / total * 100, 2) if total else 0,
            },
            "sample_classifications": lot_results[:20],  # 前20条示例
        },
        "cycle_train_candidates": {
            "total_candidates": len(train_candidates),
            "candidates_summary": [
                {
                    "group_id": c["group_id"],
                    "transport_mode": c["transport_mode"],
                    "lot": c["lot"],
                    "wagon_count": c["wagon_count"],
                    "car_no_count": c.get("car_no_count", 0),
                    "time_range": f"{c['start_time'][:16]} ~ {c['end_time'][:16]}",
                    "ydid_count": len(c["ydids"]),
                }
                for c in train_candidates
            ],
            "sample_detail": train_candidates[:5],  # 前5组详情
        },
        "analysis_timestamp": datetime.now().isoformat(),
        "query_scope": {
            "cargo_name": "大豆",
            "origin": "高桥镇",
            "destination": "新台子",
        }
    }


def main():
    print("=== 九三大豆 95306 数据只读分析 (Phase 1) ===")

    records = query_95306()
    print(f"\n从 rail95306-sync DB 查询到 {len(records)} 条大豆→新台子记录")

    result = analyze(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n分析结果已写入: {OUTPUT_PATH}")

    # 打印摘要
    print(f"\n--- 摘要 ---")
    print(f"总记录数: {result['total_records']}")
    print(f"唯一车号: {result['unique_car_nos']}")
    print(f"运输方式: {json.dumps(result['transport_mode_distribution'], ensure_ascii=False)}")
    print(f"箱号覆盖率: {result['container_coverage']['container_coverage_pct']}%")
    print(f"Lot分类: lot01={result['lot_classification']['lot01_count']}, lot02={result['lot_classification']['lot02_count']}")
    print(f"循环列候选数: {result['cycle_train_candidates']['total_candidates']}")
    print("=== 分析完成 ===")


if __name__ == "__main__":
    main()
