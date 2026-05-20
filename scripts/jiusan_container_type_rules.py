#!/usr/bin/env python3
"""
九三大豆 — 箱型/车型识别与重量规则

提供：
  classify_container_type(container_no, freight_fee=None)
  container_business_weight(container_type)
  parse_container_numbers(container_numbers_json, container_no_raw=None)
  classify_bulk_wagon_weight(car_model)

规则来源：
  orders/2026-05-20_hermes_jiusan_phase2_patch_container_type_weight_rules.md
  orders/2026-05-20_hermes_jiusan_phase2_patch_bulk_wagon_weight_rules.md

使用：
    from scripts.jiusan_container_type_rules import (
        classify_container_type, classify_bulk_wagon_weight,
        container_business_weight, parse_container_numbers,
    )
"""
import json
from typing import Optional

# ── 箱型常量 ──

OPEN_TOP = "open_top"       # 敞顶箱（长顶箱）
TOP_OPEN = "top_open"        # 顶开门箱
UNKNOWN_TYPE = "unknown"     # 未知箱型

# ── 箱型重量 ──

CONTAINER_WEIGHTS = {
    OPEN_TOP: 28.5,   # 敞顶箱：平均装货 28.5 吨/箱
    TOP_OPEN: 26.7,   # 顶开门箱：平均装货 26.7 吨/箱
}

# ── 散粮车型重量 ──

BULK_WAGON_WEIGHTS = {
    "L18": 60.0,   # 散粮专用车 60 吨/车
    "L70": 69.0,   # 散粮专用车 69 吨/车
}

# ── 顶开门箱 TBJU 后 3 位区间 ──

TOP_OPEN_TBJU_RANGES = [
    (594, 620),
    (705, 711),
    (758, 781),
]


def _tbju_is_top_open(suffix_3: int) -> bool:
    """判断 TBJU 后 3 位数字是否属于顶开门箱区间"""
    for lo, hi in TOP_OPEN_TBJU_RANGES:
        if lo <= suffix_3 <= hi:
            return True
    return False


def classify_container_type(container_no: str, freight_fee: Optional[int] = None) -> str:
    """判断单个箱号的箱型

    Args:
        container_no: 箱号字符串（如 "TBJU5940000", "TBCU0405966"）
        freight_fee: 可选，95306 freight_fee 整数分

    Returns:
        "open_top"（敞顶箱）| "top_open"（顶开门箱）| "unknown"
    """
    # freight_fee 优先作为强校验
    if freight_fee == 176710:
        return TOP_OPEN
    if freight_fee == 193260:
        return OPEN_TOP

    if not container_no:
        return UNKNOWN_TYPE

    no = container_no.strip().upper()

    # TBCU → 敞顶箱
    if no.startswith("TBCU"):
        return OPEN_TOP

    # TBJU → 按后 3 位判断
    if no.startswith("TBJU"):
        # 如果箱号有足够的数字位
        if len(no) >= 7:
            try:
                suffix = int(no[4:7])
            except ValueError:
                return OPEN_TOP  # 非数字，保守判为敞顶箱
            if _tbju_is_top_open(suffix):
                return TOP_OPEN
            return OPEN_TOP
        # 箱号过短无法判断，保守判为敞顶箱
        if len(no) >= 5:
            return OPEN_TOP

    return UNKNOWN_TYPE


def container_business_weight(container_type: str) -> float:
    """返回指定箱型的单箱业务重量（吨）

    Returns:
        重量吨数。未知箱型返回 0。
    """
    return CONTAINER_WEIGHTS.get(container_type, 0.0)


def parse_container_numbers(
    container_numbers_json: Optional[str] = None,
    container_no_raw: Optional[str] = None,
    freight_fee: Optional[int] = None,
) -> dict:
    """解析一车两个集装箱的箱号，并统计箱型

    Args:
        container_numbers_json: 95306 的 container_numbers_json 字段
        container_no_raw: 备选 raw 字段
        freight_fee: 该车的 freight_fee（作为强校验，两箱运费相同）

    Returns:
        {
            "container_count": 2,
            "containers": [{"no": "TBJUxxx", "type": "open_top"}, ...],
            "open_top_count": 1,
            "top_open_count": 1,
            "unknown_count": 0,
        }
    """
    containers = []

    # 尝试从 JSON 解析
    numbers = []
    if container_numbers_json:
        try:
            parsed = json.loads(container_numbers_json) if isinstance(container_numbers_json, str) else container_numbers_json
            if isinstance(parsed, list):
                numbers = [str(n) for n in parsed if n]
        except (json.JSONDecodeError, TypeError):
            pass

    # 后备：从 raw 字段按分隔符拆分
    if not numbers and container_no_raw:
        import re
        numbers = re.split(r'[,\s/]+', container_no_raw.strip())
        numbers = [n.strip() for n in numbers if n.strip()]

    # 剩余后备：从 JSON 字符串中提取 TBJU/TBCU 编号
    if not numbers and container_numbers_json:
        import re
        raw = str(container_numbers_json)
        numbers = re.findall(r'(TBJU\d+|TBCU\d+)', raw)

    # 最多取 2 个
    numbers = numbers[:2]

    for no in numbers:
        ctype = classify_container_type(no, freight_fee=freight_fee)
        containers.append({"no": no, "type": ctype})

    open_top = sum(1 for c in containers if c["type"] == OPEN_TOP)
    top_open = sum(1 for c in containers if c["type"] == TOP_OPEN)
    unknown = sum(1 for c in containers if c["type"] == UNKNOWN_TYPE)

    return {
        "container_count": len(containers),
        "containers": containers,
        "open_top_count": open_top,
        "top_open_count": top_open,
        "unknown_count": unknown,
    }


def classify_bulk_wagon_weight(car_model: str) -> Optional[float]:
    """返回散粮车型的业务重量（吨/车）

    Returns:
        重量吨数。未知车型返回 None。
    """
    return BULK_WAGON_WEIGHTS.get(car_model, None)


# ── 批量统计函数 ──


def calc_container_train_weight(container_records: list[dict]) -> dict:
    """计算一批车（一列）的集装箱重量统计

    Args:
        container_records: 每车 dict，至少包含
            container_numbers_json, container_no_raw, freight_fee

    Returns:
        {
            "open_top_count": int,
            "top_open_count": int,
            "unknown_count": int,
            "open_top_weight_tons": float,
            "top_open_weight_tons": float,
            "business_weight_tons": float,
            "rail_marked_weight_tons": float,
            "weight_diff_tons": float,
            "weight_source": "container_type_rule",
        }
    """
    total_open_top = 0
    total_top_open = 0
    total_unknown = 0
    total_marked = 0.0

    for rec in container_records:
        parsed = parse_container_numbers(
            rec.get("container_numbers_json"),
            rec.get("container_no_raw"),
            rec.get("freight_fee"),
        )
        total_open_top += parsed["open_top_count"]
        total_top_open += parsed["top_open_count"]
        total_unknown += parsed["unknown_count"]

        # marked_weight
        mw = rec.get("marked_weight")
        if mw:
            try:
                total_marked += float(mw)
            except (ValueError, TypeError):
                pass

    open_top_weight = total_open_top * CONTAINER_WEIGHTS[OPEN_TOP]
    top_open_weight = total_top_open * CONTAINER_WEIGHTS[TOP_OPEN]
    business_weight = round(open_top_weight + top_open_weight, 2)
    rail_marked = round(total_marked, 2)

    return {
        "open_top_count": total_open_top,
        "top_open_count": total_top_open,
        "unknown_count": total_unknown,
        "open_top_weight_tons": round(open_top_weight, 2),
        "top_open_weight_tons": round(top_open_weight, 2),
        "business_weight_tons": business_weight,
        "rail_marked_weight_tons": rail_marked,
        "weight_diff_tons": round(business_weight - rail_marked, 2),
        "weight_source": "container_type_rule",
    }


def calc_bulk_wagon_weight(bulk_records: list[dict]) -> dict:
    """计算散粮车重量统计

    Args:
        bulk_records: 每车 dict，至少包含 car_model、marked_weight

    Returns:
        {
            "L18_count": int,
            "L70_count": int,
            "unknown_count": int,
            "business_weight_tons": float,
            "rail_marked_weight_tons": float,
            "weight_diff_tons": float,
            "weight_source": "bulk_wagon_model_rule",
        }
    """
    l18 = 0
    l70 = 0
    unknown = 0
    total_marked = 0.0

    for rec in bulk_records:
        model = rec.get("car_model", "").strip().upper()
        if model == "L18":
            l18 += 1
        elif model == "L70":
            l70 += 1
        else:
            unknown += 1

        mw = rec.get("marked_weight")
        if mw:
            try:
                total_marked += float(mw)
            except (ValueError, TypeError):
                pass

    business = l18 * BULK_WAGON_WEIGHTS["L18"] + l70 * BULK_WAGON_WEIGHTS["L70"]
    rail_marked = round(total_marked, 2)

    return {
        "L18_count": l18,
        "L70_count": l70,
        "unknown_count": unknown,
        "business_weight_tons": round(business, 2),
        "rail_marked_weight_tons": rail_marked,
        "weight_diff_tons": round(business - rail_marked, 2),
        "weight_source": "bulk_wagon_model_rule",
    }
