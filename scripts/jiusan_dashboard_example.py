#!/usr/bin/env python3
"""
生成模拟 jiusan_dashboard_data.example.json (Phase 1)

基于 95306 只读数据 + 手动模拟值生成看板 JSON 样例。
不写入任何生产数据。
"""
import json
from datetime import datetime
from pathlib import Path

ANALYSIS_PATH = Path(__file__).resolve().parent.parent / "samples" / "jiusan_95306_analysis.json"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "samples" / "jiusan_dashboard_data.example.json"


def generate(analysis: dict) -> dict:
    """基于分析和手动模拟值生成看板 JSON"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    # 从分析数据中提取实际统计
    mode_dist = analysis.get("transport_mode_distribution", {})
    container_total = mode_dist.get("集装箱运输", 0)
    bulk_total = mode_dist.get("整车运输", 0)

    # 从 95306 最近数据估算日发运量
    daily = analysis.get("daily_distribution", {})
    recent_days = sorted(daily.keys())[-7:]
    recent_totals = [daily[d] for d in recent_days]
    avg_daily = sum(recent_totals) / len(recent_totals) if recent_totals else 0

    # 循环列候选摘要
    candidates = analysis.get("cycle_train_candidates", {})
    candidate_summary = candidates.get("candidates_summary", [])

    # 查找最近一批集装箱发运和散粮发运
    last_container = [c for c in candidate_summary if c["lot"] == "lot01"]
    last_bulk = [c for c in candidate_summary if c["lot"] == "lot02"]

    dashboard = {
        "_meta": {
            "dashboard_name": "九三大豆循环运输看板",
            "generated_at": now.isoformat(),
            "data_sources": ["95306_sync(read_only)", "manual_simulated"],
            "note": "Phase 1 only: 基于真实 95306 数据 + 手动模拟值演示。不写入任何生产库。",
            "95306_data_date_range": "2026-03-19 ~ 2026-05-20",
        },
        "daily_summary": {
            "date": date_str,
            "loaded_today": round(avg_daily * 0.6),
            "dispatched_today": round(avg_daily * 0.55),
            "arrived_today": round(avg_daily * 0.5),
            "unloaded_today": round(avg_daily * 0.45),
            "daily_plan_target": 12000,
            "daily_plan_achieved_tons": 10560,
            "daily_achievement_rate_pct": 88.0,
            "rolling_3d_achieved_tons": 34560,
            "rolling_3d_target": 36000,
            "rolling_3d_achievement_rate_pct": 96.0,
            "machine_readable_summary": {
                "loaded": round(avg_daily * 0.6),
                "dispatched": round(avg_daily * 0.55),
                "arrived": round(avg_daily * 0.5),
                "unloaded": round(avg_daily * 0.45),
                "avg_daily_95306": round(avg_daily, 1),
                "total_95306_records": analysis["total_records"],
                "unique_cars_95306": analysis["unique_car_nos"],
            }
        },
        "active_plan": {
            "plan_id": "plan_B",
            "name": "第二轮调整计划",
            "effective_from": "2026-05-16",
            "details": {
                "container": {"daily_cols": 2, "frequency": "2天3列"},
                "bulk_wagon": {"daily_cols": 1, "frequency": "1天1列"},
                "factory_consumption": 5500,
                "daily_target": 12000,
            }
        },
        "container_resources": {
            "total_active": 312,
            "total_from_95306": container_total,
            "distribution": {
                "port_empty": 80,
                "port_loaded": 56,
                "in_transit_loaded": 72,
                "xintaizi_330_loaded": 48,
                "xintaizi_330_empty": 24,
                "return_empty": 32,
            },
            "adjustments_recent": [
                {"date": "2026-05-19", "type": "transfer_in", "qty": 20, "note": "从其他项目调入（模拟）"},
            ]
        },
        "wagon_resources": {
            "total_active": 160,
            "total_from_95306_soybean": bulk_total,
            "distribution": {
                "port": 40,
                "in_transit": 80,
                "arrived_factory": 40,
            },
            "adjustments_recent": [],
        },
        "cycle_trains": [
            {
                "train_id": c["group_id"],
                "type": "集装箱" if c["lot"] == "lot01" else "散粮",
                "lot": c["lot"],
                "status": "on_way",
                "depart_info": {
                    "time": c["time_range"].split(" ~ ")[0],
                    "wagon_count": c["wagon_count"],
                    "car_no_count": c.get("car_no_count", 0),
                }
            }
            for c in (last_container[-1:] + last_bulk[-1:])
        ],
        "three_account_check": {
            "check_summary": {
                "total_checks": 6,
                "passed": 5,
                "failed": 1,
                "pending": 0,
            },
            "checks": [
                {
                    "node": "port_loaded_container",
                    "formula": "今日 = 昨日 + 昨日装箱 - 昨日发出",
                    "expected": 56,
                    "actual": 56,
                    "match": True,
                    "adjustment": 0,
                },
                {
                    "node": "in_transit_loaded_container",
                    "formula": "今日在途 = 昨在途 + 昨发出 - 昨到达",
                    "expected": 72,
                    "actual": 74,
                    "match": False,
                    "adjustment": 0,
                    "discrepancy": 2,
                    "discrepancy_note": "待解释差异（模拟）",
                },
                {
                    "node": "xintaizi_330_loaded",
                    "formula": "今日330重箱 = 昨330重箱 + 昨到达 - 昨卸空",
                    "expected": 48,
                    "actual": 48,
                    "match": True,
                    "adjustment": 0,
                },
                {
                    "node": "return_trip_empty",
                    "formula": "今日返空 = 昨返空 + 昨卸空 - 昨返港",
                    "expected": 32,
                    "actual": 32,
                    "match": True,
                    "adjustment": 0,
                },
                {
                    "node": "port_bulk_wagon",
                    "formula": "今日港口散粮车 = 昨港口 + 昨返空 - 昨发散粮",
                    "expected": 40,
                    "actual": 40,
                    "match": True,
                    "adjustment": 0,
                },
                {
                    "node": "in_transit_bulk",
                    "formula": "今日在途散粮 = 昨在途 + 昨发散粮 - 昨到散粮",
                    "expected": 80,
                    "actual": 82,
                    "match": False,
                    "adjustment": 0,
                    "discrepancy": 2,
                    "discrepancy_note": "待解释差异（模拟）",
                },
            ],
        },
        "factory_inventory": {
            "current_stock": 45000,
            "red_line": 20000,
            "warning_level": "normal",
            "days_supported": 8.2,
            "today_consumption": 5500,
            "today_line_in": 5280,
            "today_other_source": 0,
            "other_source_inferred": 220,
            "trend_7d": [42000, 43500, 44000, 45000, 46000, 45500, 45000],
            "note": "7日趋势为模拟值。其他来源入库通过差额反推公式计算。",
        },
        "pending_confirmations": [
            {
                "type": "train_membership",
                "ydid": "模拟示例1",
                "car_no": "XXXXXX",
                "question": "该车辆应归属哪列循环列？",
                "candidates": [c["group_id"] for c in (last_container[-3:] + last_bulk[-3:])],
            },
            {
                "type": "inventory_discrepancy",
                "detail": "其他来源入库差额 -220 吨需确认（模拟）",
            },
            {
                "type": "column_20_review",
                "detail": f"最近循环列候选 {len(last_container)} 组集装箱 + {len(last_bulk)} 组散粮，建议人工确认列归属",
            },
        ],
        "95306_scan_status": {
            "last_scan": datetime.now().isoformat(),
            "new_records_found": analysis.get("total_records", 0),
            "matched_to_lot": analysis["lot_classification"]["lot01_count"] + analysis["lot_classification"]["lot02_count"],
            "matched_to_train": len(candidate_summary),
            "pending_lot_assignment": 0,
            "pending_train_assignment": len(candidate_summary),
            "errors_last_scan": None,
        },
    }

    return dashboard


def main():
    if not ANALYSIS_PATH.exists():
        print(f"[ERROR] 95306 analysis not found. Run jiusan_95306_analysis.py first.")
        return

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    dashboard = generate(analysis)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2, default=str)

    print(f"模拟看板 JSON 已生成: {OUTPUT_PATH}")
    print(f"  总区块: {len(dashboard)}")
    print(f"  循环列: {len(dashboard['cycle_trains'])} 列")


if __name__ == "__main__":
    main()
