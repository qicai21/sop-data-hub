from __future__ import annotations

OPEN_TOP = "open_top"
TOP_OPEN = "top_open"
UNKNOWN_TYPE = "unknown"

CONTAINER_WEIGHTS = {
    OPEN_TOP: 28.5,
    TOP_OPEN: 26.7,
}

TOP_OPEN_TBJU_RANGES = (
    (594, 620),
    (705, 711),
    (758, 781),
)

# 2026-09-14 95306 实时核验：TBCU317/TBCU318/TBCU319 段为新投入的
# 顶开门大豆箱（295 个 unique 箱号，9 月 6 日起进入九三循环）。TBCU 前缀
# 不能再一概按敞顶箱处理；其余 TBCU 仍维持既有敞顶口径。
TOP_OPEN_TBCU_RANGES = (
    (317, 319),
)


def classify_container_type(container_no: str | None) -> str:
    """Classify a Jiusan container from its business number range."""
    no = str(container_no or "").strip().upper()
    if no.startswith("TBCU"):
        if len(no) < 7:
            return OPEN_TOP
        try:
            range_code = int(no[4:7])
        except ValueError:
            return OPEN_TOP
        if any(lower <= range_code <= upper for lower, upper in TOP_OPEN_TBCU_RANGES):
            return TOP_OPEN
        return OPEN_TOP
    if not no.startswith("TBJU"):
        return UNKNOWN_TYPE
    if len(no) < 7:
        return OPEN_TOP if len(no) >= 5 else UNKNOWN_TYPE
    try:
        range_code = int(no[4:7])
    except ValueError:
        return OPEN_TOP
    if any(lower <= range_code <= upper for lower, upper in TOP_OPEN_TBJU_RANGES):
        return TOP_OPEN
    return OPEN_TOP


def container_business_weight(container_type: str) -> float:
    return CONTAINER_WEIGHTS.get(container_type, 0.0)


__all__ = [
    "CONTAINER_WEIGHTS",
    "OPEN_TOP",
    "TOP_OPEN",
    "TOP_OPEN_TBCU_RANGES",
    "TOP_OPEN_TBJU_RANGES",
    "UNKNOWN_TYPE",
    "classify_container_type",
    "container_business_weight",
]
