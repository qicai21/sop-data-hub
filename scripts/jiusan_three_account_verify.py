#!/usr/bin/env python3
"""
三账校验验证脚本 (Phase 1)

验证 Snapshot + Flow + Adjustment 三账平衡公式。
纯计算验证，不写入任何数据库。

Usage:
    python3 scripts/jiusan_three_account_verify.py
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "samples" / "jiusan_three_account_sample.json"


# ========== 模拟数据 ==========

SAMPLE_SNAPSHOT_T = {
    "date": "2026-05-19",
    "source": "morning_report",
    "port_loaded_container": 48,
    "port_empty_container": 88,
    "in_transit_loaded_container": 64,
    "330_line_loaded_container": 40,
    "330_line_empty_container": 20,
    "return_trip_empty_container": 28,
    "port_bulk_wagon": 40,
    "in_transit_bulk_wagon": 72,
    "arrived_bulk_wagon": 32,
}

SAMPLE_FLOW_T = {
    "date": "2026-05-19",
    "source": "morning_report",
    "yesterday_loaded_container": 28,
    "yesterday_dispatched_container": 24,
    "yesterday_arrived_container": 20,
    "yesterday_unloaded_container": 16,
    "yesterday_returned_empty": 12,
    "yesterday_dispatched_bulk": 40,
    "yesterday_arrived_bulk": 36,
}

SAMPLE_ADJUSTMENT_T = {
    "date": "2026-05-19",
    "events": [
        {"type": "transfer_in", "pool_type": "container", "quantity": 20, "note": "从其他项目调入"},
        {"type": "repair", "pool_type": "container", "quantity": 2, "note": "送修2箱"},
    ],
}

SAMPLE_SNAPSHOT_T1 = {
    "date": "2026-05-20",
    "source": "95306_calc",
    "port_loaded_container": 52,
    "port_empty_container": 92,
    "in_transit_loaded_container": 68,
    "330_line_loaded_container": 44,
    "330_line_empty_container": 24,
    "return_trip_empty_container": 32,
    "port_bulk_wagon": 0,
    "in_transit_bulk_wagon": 76,
    "arrived_bulk_wagon": 68,
}


# ========== 校验引擎 ==========


class ThreeAccountVerifier:
    """三账校验引擎 — 纯计算，不落库"""

    def __init__(self, snapshot_t: dict, flow: dict, adjustment: dict, snapshot_t1: dict):
        self.snapshot_t = snapshot_t
        self.flow = flow
        self.adjustment = adjustment
        self.snapshot_t1 = snapshot_t1
        self.results = []

    def add_check(self, name: str, prev_field: str, plus_field: str,
                  minus_field: str, expected_field: str,
                  adjustment: int = 0):
        """添加一个校验检查

        expected = prev_value + plus_value - minus_value + adjustment
        """
        prev = self.snapshot_t.get(prev_field)
        plus = self.flow.get(plus_field, 0) if plus_field else 0
        minus = self.flow.get(minus_field, 0) if minus_field else 0
        expected = self.snapshot_t1.get(expected_field)

        if prev is None or expected is None:
            self.results.append({
                "node": name,
                "status": "skip",
                "reason": f"缺失字段: prev={prev}, expected={expected}",
            })
            return

        calculated = prev + plus - minus + adjustment
        match = (calculated == expected)

        self.results.append({
            "node": name,
            "formula": f"{name} = {prev}(期初) + {plus}(流入) - {minus}(流出) + {adjustment}(调整)",
            "expected_value": expected,
            "calculated_value": calculated,
            "match": match,
            "discrepancy": None if match else expected - calculated,
            "adjustment": adjustment,
        })

    def add_cross_check(self, name: str, formula_desc: str,
                        calc_value: Any, reported_value: Any,
                        threshold: float = 0):
        """添加手动交叉校验"""
        match = abs(float(calc_value) - float(reported_value)) <= threshold
        self.results.append({
            "node": name,
            "formula": formula_desc,
            "expected_value": reported_value,
            "calculated_value": calc_value,
            "match": match,
            "discrepancy": None if match else float(reported_value) - float(calc_value),
            "adjustment": 0,
        })

    @property
    def summary(self) -> dict:
        passed = sum(1 for r in self.results if r.get("match"))
        failed = sum(1 for r in self.results if r.get("match") is False)
        skipped = sum(1 for r in self.results if r.get("status") == "skip")
        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }

    def to_dict(self) -> dict:
        return {
            "verifier": "jiusan_three_account_verifier_v0.1",
            "phase": "1",
            "description": "三账校验验证样例 — 纯计算，不落库",
            "input_data": {
                "snapshot_t": self.snapshot_t,
                "flow": self.flow,
                "adjustment": self.adjustment,
                "snapshot_t1": self.snapshot_t1,
            },
            "checks": self.results,
            "summary": self.summary,
        }


# ========== 运行验证 ==========


def run_verify() -> dict:
    """运行所有校验检查"""

    v = ThreeAccountVerifier(
        SAMPLE_SNAPSHOT_T, SAMPLE_FLOW_T,
        SAMPLE_ADJUSTMENT_T, SAMPLE_SNAPSHOT_T1
    )

    # --- 集装箱链校验 ---

    # 1. 港口重箱: 今日 = 昨日港口重箱 + 昨日装箱 - 昨日发出 ± 调整
    adj_container_in = sum(
        e["quantity"] for e in SAMPLE_ADJUSTMENT_T["events"]
        if e["pool_type"] == "container" and e["type"] in ("transfer_in", "add")
    )
    adj_container_out = sum(
        e["quantity"] for e in SAMPLE_ADJUSTMENT_T["events"]
        if e["pool_type"] == "container" and e["type"] in ("transfer_out", "repair")
    )
    v.add_check(
        "港口重箱 (port_loaded_container)",
        "port_loaded_container", "yesterday_loaded_container", "yesterday_dispatched_container",
        "port_loaded_container",
        adjustment=(adj_container_in - adj_container_out)
    )

    # 2. 在途重箱: 今日在途 = 昨在途 + 昨日发出 - 昨日到达
    v.add_check(
        "铁路在途重箱 (in_transit_loaded_container)",
        "in_transit_loaded_container", "yesterday_dispatched_container", "yesterday_arrived_container",
        "in_transit_loaded_container"
    )

    # 3. 330处重箱: 今日重箱 = 昨重箱 + 昨日到达 - 昨日卸空
    v.add_check(
        "330处专用线重箱 (330_line_loaded_container)",
        "330_line_loaded_container", "yesterday_arrived_container", "yesterday_unloaded_container",
        "330_line_loaded_container"
    )

    # 4. 返程空箱: 今日返空 = 昨返空 + 昨日卸空 - 昨日返港
    v.add_check(
        "返程空箱 (return_trip_empty_container)",
        "return_trip_empty_container", "yesterday_unloaded_container", "yesterday_returned_empty",
        "return_trip_empty_container"
    )

    # 5. 港口空箱: 今日空箱 = 昨空箱 - 昨日装箱 + 昨日返空 + 调整
    v.add_check(
        "港口空箱 (port_empty_container)",
        "port_empty_container", "yesterday_returned_empty", "yesterday_loaded_container",
        "port_empty_container",
        adjustment=adj_container_in
    )

    # --- 散粮车链校验 ---

    # 6. 港口散粮车: 今日 = 昨港口 + 昨返空 - 昨发散粮 (散粮车无固定返空线路)
    v.add_check(
        "港内散粮车 (port_bulk_wagon)",
        "port_bulk_wagon", "", "yesterday_dispatched_bulk",
        "port_bulk_wagon"
    )

    # 7. 在途散粮车: 今日在途 = 昨在途 + 昨日发散粮 - 昨日到散粮
    v.add_check(
        "在途散粮车 (in_transit_bulk_wagon)",
        "in_transit_bulk_wagon", "yesterday_dispatched_bulk", "yesterday_arrived_bulk",
        "in_transit_bulk_wagon"
    )

    # 8. 到站散粮车: 今日到站 = 昨到站 + 昨日到散粮 - 昨日卸空 (K车到站即卸)
    v.add_check(
        "到站散粮车 (arrived_bulk_wagon)",
        "arrived_bulk_wagon", "yesterday_arrived_bulk", "",
        "arrived_bulk_wagon"
    )

    # --- 厂家库存校验 ---
    inv_opening = 42000
    inv_line_in = 5280
    inv_other = 220
    inv_consumption = 5500
    inv_adjustment = 0
    inv_closing = 45000

    inv_calc = inv_opening + inv_line_in + inv_other - inv_consumption + inv_adjustment
    inv_other_reverse = inv_closing - inv_opening - inv_line_in + inv_consumption - inv_adjustment

    v.add_cross_check(
        "厂家库存 (factory_inventory)",
        f"期末 = {inv_opening} + {inv_line_in} + {inv_other} - {inv_consumption}",
        inv_calc, inv_closing
    )
    v.add_cross_check(
        "其他来源入库反推 (other_source_inferred)",
        f"其他来源 = {inv_closing} - {inv_opening} - {inv_line_in} + {inv_consumption}",
        inv_other_reverse, inv_other
    )

    return v.to_dict()


def main():
    result = run_verify()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print("=== 三账校验验证 (Phase 1) ===")
    print(f"样例输出: {OUTPUT_PATH}")
    print(f"\n校验结果摘要:")
    print(f"  总检查项: {result['summary']['total']}")
    print(f"  通过: {result['summary']['passed']} ✅")
    print(f"  失败: {result['summary']['failed']} ❌")
    print(f"  跳过: {result['summary']['skipped']} ⏭️")
    print()
    for c in result["checks"]:
        icon = "✅" if c.get("match") else ("❌" if c.get("match") is False else "⏭️")
        label = c.get("node", "?")
        if c.get("match") is False:
            print(f"  {icon} {label}: 期望={c['expected_value']}, 计算={c['calculated_value']}, 差异={c['discrepancy']}")
        else:
            print(f"  {icon} {label}")
    print("\n=== 验证完成 ===")


if __name__ == "__main__":
    main()
