#!/usr/bin/env python3
"""
Phase 1 验证脚本 — 检验 V3 看板的正确性

验证点：
1. 运行状态以 95306 最新事件为准单向映射
2. 无冲突检测残留
3. 列二状态 = 已发车（在途）
4. 资源池 / 预警 / 计划按日明细 数据正确
"""
import json
import sys
from pathlib import Path

DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "jiusan_dashboard_data.json"

errors = []
passes = []


def check(condition: bool, msg: str):
    if condition:
        passes.append(f"✅ {msg}")
    else:
        errors.append(f"❌ {msg}")


# 1. 读取看板
if not DASHBOARD_PATH.exists():
    print("❌ 看板 JSON 不存在，请先运行 python3 scripts/jiusan_board_generate.py")
    sys.exit(1)

dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))

# 2. 元信息
meta = dashboard.get("_meta", {})
check("source_conflict" not in json.dumps(dashboard), "JSON 中无冲突检测残留")
check(meta.get("version") == "3.0", f"看板版本为 V3（实际: {meta.get('version')}）")
check("循环运输资源账" in meta.get("architecture", ""), "架构声明正确")

# 3. 设计原则
principles = meta.get("design_principles", [])
check("不搞预期 vs 实际冲突检测" in principles, "设计原则包含\"不搞冲突检测\"")
check("运行状态以 95306 最新事件为准单向映射" in principles, "设计原则包含\"单向映射\"")

# 4. 循环列状态
trains = dashboard.get("cycle_trains", [])
check(len(trains) >= 2, f"至少有 2 列（实际: {len(trains)}）")

for t in trains:
    tid = t["train_id"]
    tracking = t.get("tracking", {})
    status_text = t.get("status_text", "N/A")
    summary = tracking.get("summary_status", "N/A")

    if "02" in tid:
        check(
            summary in ("已发车（在途）", "已发车"),
            f"{tid}: status_text={status_text}, summary={summary} — 应为已发车",
        )
        check(
            tracking.get("departed_cars", 0) == 54,
            f"{tid}: 已发车 {tracking.get('departed_cars')}/54 车",
        )
        check(
            tracking.get("arrived_cars", 0) == 0,
            f"{tid}: 已到达 {tracking.get('arrived_cars')} 车（应为 0，刚发车）",
        )
    elif "01" in tid:
        # Train 01 已有 tracking 数据
        check(
            status_text in ("待同步", "已到达", "已交付") or "已发车" in status_text,
            f"{tid}: status_text={status_text}",
        )

# 5. 资源池
pool = dashboard.get("resource_pool", {})
check("container" in pool, "集装箱池存在")
check("wagon" in pool, "车体池存在")
if "container" in pool:
    check(pool["container"].get("current_total", 0) > 0, "集装箱池 total > 0")
if "wagon" in pool:
    check(pool["wagon"].get("current_total", 0) > 0, "车体池 total > 0")

# 6. 预警
warnings = dashboard.get("warnings", [])
check(len(warnings) >= 1, f"至少有 1 条预警（实际: {len(warnings)}）")
if warnings:
    check(warnings[0].get("message", "").startswith("库存"), "预警消息为库存告警")

# 7. 发运计划
plan_info = dashboard.get("current_plan", {}).get("plan", {})
check(plan_info.get("id") == "plan_B", f"当前计划为 plan_B（实际: {plan_info.get('id')}）")
plan_days = dashboard.get("current_plan", {}).get("plan_days", [])
check(len(plan_days) == 7, f"计划按日明细 7 天（实际: {len(plan_days)}）")

# 8. 库存
inv = dashboard.get("factory_inventory", {}) or {}
if inv:
    check(inv.get("below_red_line") == True, "库存低于红线标记正确")
    check(inv.get("data_quality") == "user_reported", "数据质量标记为 user_reported")

# 9. summary 区块无冲突引用
summary = dashboard.get("summary", {})
check("source_conflict" not in str(summary), "summary 中无 conflict 引用")

# 10. 三账区块
three = dashboard.get("three_accounts", {})
check("snapshots" in three, "三账包含 snapshots")
check("flows" in three, "三账包含 flows")
check("adjustments" in three, "三账包含 adjustments")

# ── 结果 ──
print(f"\n{'=' * 50}")
print(f"V3 看板验证结果")
print(f"{'=' * 50}")
for p in passes:
    print(p)
for e in errors:
    print(e)
print(f"\n通过: {len(passes)} / {len(passes) + len(errors)}")
if errors:
    print("❌ 有验证失败项")
    sys.exit(1)
else:
    print("✅ 全部通过")
