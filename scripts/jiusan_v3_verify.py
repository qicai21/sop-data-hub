#!/usr/bin/env python3
"""
Phase 1 V3 收口修正 — 验证脚本

验证点：
1. JSON 中 source_conflicts 字段存在（可为空数组），但无旧版冲突报警残留
2. 列一 = 已交付 54/54，列二 = 已发车（在途）
3. 船名玛格丽特，lot1/lot2 精确值
4. 重量口径正确
5. active_plan_human 存在
6. 资源池/预警/计划按日明细数据正确
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


if not DASHBOARD_PATH.exists():
    print("❌ 看板 JSON 不存在")
    sys.exit(1)

dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))

# 1. 元信息
meta = dashboard.get("_meta", {})
check(meta.get("version") == "3.0", f"看板版本为 V3（实际: {meta.get('version')}）")
check("循环运输资源账" in meta.get("architecture", ""), "架构声明正确")
check("source_conflicts 保留" in str(meta.get("design_principles", [])), "设计原则保留 source_conflicts")
check("JGWL-JZTS-DD-202601" in str(meta.get("contract", {})), "合同编号存在")

# 2. source_conflicts — 字段存在（可为空）
sc = dashboard.get("source_conflicts", None)
check(sc is not None, "source_conflicts 字段存在")
check(isinstance(sc, list), "source_conflicts 为数组类型")
check(len(sc) == 0, "source_conflicts 当前为空（无冲突）")

# 3. 旧冲突关键词不在描述字段中出现
descriptions = json.dumps({
    "status_text": [t.get("status_text", "") for t in dashboard.get("cycle_trains", [])],
    "summary": dashboard.get("summary", ""),
})
check("⚠️ 人工确认" not in descriptions, "无旧版冲突报警描述残留")

# 4. 循环列状态
trains = dashboard.get("cycle_trains", [])
check(len(trains) >= 2, f"至少有 2 列（实际: {len(trains)}）")

for t in trains:
    tid = t["train_id"]
    tracking = t.get("tracking", {})
    status_text = t.get("status_text", "N/A")
    last_run = t.get("last_run", {})

    if "02" in tid:
        check(
            "已发车" in status_text,
            f"{tid}: status_text={status_text} — 应为已发车",
        )
        check(tracking.get("departed_cars", 0) == 54, f"{tid}: 已发车 54/54")
        check(tracking.get("arrived_cars", 0) == 0, f"{tid}: 已到达 0（刚发车）")
        # 重量口径：业务重量 2998.8, 标重 3456.0, 差异 -457.2
        cts = last_run.get("container_type_summary", {}) or {}
        if cts:
            check(cts.get("business_weight_tons") == 2998.8, f"{tid}: 业务重量 2998.8（实际: {cts.get('business_weight_tons')}）")
            check(cts.get("rail_marked_weight_tons") == 3456.0, f"{tid}: 95306标重 3456.0（实际: {cts.get('rail_marked_weight_tons')}）")
    elif "01" in tid:
        check(
            "已交付" in status_text,
            f"{tid}: status_text={status_text} — 应为已交付",
        )
        check(tracking.get("delivered_cars", 0) == 54, f"{tid}: 已交付 54/54")
        # 重量口径：业务重量 3067.2, 标重 3456.0, 差异 -388.8
        cts = last_run.get("container_type_summary", {}) or {}
        if cts:
            check(cts.get("business_weight_tons") == 3067.2, f"{tid}: 业务重量 3067.2（实际: {cts.get('business_weight_tons')}）")
            check(cts.get("rail_marked_weight_tons") == 3456.0, f"{tid}: 95306标重 3456.0（实际: {cts.get('rail_marked_weight_tons')}）")

# 5. 船名/Lot
vessel = dashboard.get("current_vessel_lots", {})
check(vessel.get("vessel_name") == "玛格丽特", f"船名=玛格丽特（实际: {vessel.get('vessel_name')}）")
lot1 = vessel.get("lot1", {})
check(lot1.get("total_planned_tons") == 48719, f"lot1 计划=48719（实际: {lot1.get('total_planned_tons')}）")
check(lot1.get("confirmed_dispatched_tons") == 6066.0, f"lot1 已发=6066.0（实际: {lot1.get('confirmed_dispatched_tons')}）")
check(lot1.get("remaining_tons") == 42653.0, f"lot1 剩余=42653.0（实际: {lot1.get('remaining_tons')}）")
lot2 = vessel.get("lot2", {})
check(lot2.get("total_planned_tons") == 20300, f"lot2 计划=20300（实际: {lot2.get('total_planned_tons')}）")
check(lot2.get("confirmed_remaining_tons") == 20051.0, f"lot2 确认剩余=20051.0（实际: {lot2.get('confirmed_remaining_tons')}）")
check(lot2.get("candidate_remaining_tons") == 17729.0, f"lot2 候选剩余=17729.0（实际: {lot2.get('candidate_remaining_tons')}）")
check(lot2.get("pending_cars") == 36, f"lot2 待确认=36（实际: {lot2.get('pending_cars')}）")

# 6. active_plan_human
plan_human = dashboard.get("active_plan_human", "")
check(len(plan_human) > 0, "active_plan_human 存在且非空")
check("第二轮调整计划" in plan_human or "计划名称" in plan_human, "计划人话摘要包含计划名称")

# 7. 资源池
pool = dashboard.get("resource_pool", {})
check("container" in pool, "集装箱池存在")
check("wagon" in pool, "车体池存在")

# 8. 预警
warnings = dashboard.get("warnings", [])
check(len(warnings) >= 1, f"至少有 1 条预警（实际: {len(warnings)}）")

# 9. 计划按日明细
plan_days = dashboard.get("current_plan", {}).get("plan_days", [])
check(len(plan_days) == 7, f"计划按日明细 7 天（实际: {len(plan_days)}）")

# 10. 散粮重量
for s in dashboard.get("one_time_shipments", []):
    bws = s.get("bulk_wagon_type_summary", {}) or {}
    if bws:
        check(bws.get("L18_count", 0) + bws.get("L70_count", 0) == 40, "散粮总车数=40")
        check(bws.get("business_weight_tons") == 2571.0, f"散粮业务重量=2571.0（实际: {bws.get('business_weight_tons')}）")
        check(bws.get("rail_marked_weight_tons") == 2571.0, f"散粮95306标重=2571.0（实际: {bws.get('rail_marked_weight_tons')}）")

# 11. source_conflicts 字段位置检查
raw = json.dumps(dashboard)
check('"source_conflicts"' in raw, "source_conflicts 字段名在 JSON 中")
check('"current_vessel_lots"' in raw, "current_vessel_lots 字段在 JSON 中")
check('"active_plan_human"' in raw, "active_plan_human 字段在 JSON 中")

# ── 结果 ──
print(f"\n{'=' * 50}")
print(f"V3 收口修正版看板验证结果")
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
