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


def classify_container_type(container_no: str | None) -> str:
    """Classify a Jiusan container from its business number range."""
    no = str(container_no or "").strip().upper()
    if no.startswith("TBCU"):
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
    "TOP_OPEN_TBJU_RANGES",
    "UNKNOWN_TYPE",
    "classify_container_type",
    "container_business_weight",
]
