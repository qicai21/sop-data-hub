from __future__ import annotations

from dataclasses import dataclass


_GROUP001_NAMES = {
    "铁晟业务工作群",
    "铁晟业务工作群-[GROUP001]",
}

_ZHONGTANG_FREIGHT_ONLY_GROUP_NAMES = {
    "中唐特钢发运群",
}

_INSPECTION_IMAGE_SOURCE_POLICY = {
    "zhongtang_special_steel": {"GROUP001"},
    "chaoyang_steel": {"GROUP001"},
    "jilin_jingang_jinzhou": {"GROUP001"},
}


@dataclass(frozen=True)
class InspectionSourceDecision:
    allowed: bool
    normalized_group_id: str
    reason: str


def _normalize_group_id(*, group_id: str | None = None, group_name: str | None = None) -> str:
    gid = str(group_id or "").strip()
    if gid:
        return gid
    gname = str(group_name or "").strip()
    if gname in _GROUP001_NAMES:
        return "GROUP001"
    if "[GROUP001]" in gname:
        return "GROUP001"
    if "[GROUP013]" in gname:
        return "GROUP013"
    if "[GROUP003]" in gname:
        return "GROUP003"
    return ""


def is_zhongtang_freight_only_group(
    *,
    group_id: str | None = None,
    group_name: str | None = None,
) -> bool:
    gid = str(group_id or "").strip()
    gname = str(group_name or "").strip()
    if gid in _ZHONGTANG_FREIGHT_ONLY_GROUP_NAMES:
        return True
    if gname in _ZHONGTANG_FREIGHT_ONLY_GROUP_NAMES:
        return True
    return False


def decide_inspection_image_source(
    *,
    project_id: str,
    group_id: str | None = None,
    group_name: str | None = None,
) -> InspectionSourceDecision:
    normalized_group_id = _normalize_group_id(group_id=group_id, group_name=group_name)
    allowed_groups = _INSPECTION_IMAGE_SOURCE_POLICY.get(str(project_id or "").strip())
    if not allowed_groups:
        return InspectionSourceDecision(
            allowed=True,
            normalized_group_id=normalized_group_id,
            reason="project_not_restricted",
        )
    if normalized_group_id in allowed_groups:
        return InspectionSourceDecision(
            allowed=True,
            normalized_group_id=normalized_group_id,
            reason="group_allowed_for_project",
        )
    return InspectionSourceDecision(
        allowed=False,
        normalized_group_id=normalized_group_id,
        reason="inspection_image_source_not_allowed",
    )
