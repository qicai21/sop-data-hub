from __future__ import annotations


DETAIL_DOC_TYPES = {
    "检装车通知单-敞车",
    "出港计划通知单",
    "耗材统计表",
}

DEFAULT_DOC_DETAIL_MODE = "shallow"


def normalize_doc_detail_mode(value: str | None) -> str:
    if not value:
        return DEFAULT_DOC_DETAIL_MODE
    normalized = value.strip().lower()
    return normalized if normalized in {"shallow", "full"} else DEFAULT_DOC_DETAIL_MODE


def should_skip_deep_detail(doc_type: str | None, detail_mode: str | None) -> bool:
    if not doc_type:
        return False
    return normalize_doc_detail_mode(detail_mode) == "shallow" and doc_type in DETAIL_DOC_TYPES
