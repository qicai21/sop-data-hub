"""Project-configured priority for sequential release-batch matching."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml


YAML_ROOT = Path(__file__).resolve().parents[3] / "config" / "project_sops"
# 手工把同一次放货拆成多个可独立匹配的子批次时，使用 lot10_a、lot10_b。
# 同一主 lot 内必须先承接 a，再承接 b，不能退回默认优先级。
_LOT_RE = re.compile(r"^lot0*(\d+)(?:_([a-z]))?$", re.IGNORECASE)


@lru_cache(maxsize=1)
def sequential_lot_priority_projects() -> frozenset[str]:
    projects: set[str] = set()
    for path in YAML_ROOT.glob("*.yaml"):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        project_id = str(payload.get("project_id") or "").strip()
        project_meta = payload.get("project_meta") or {}
        policy = (
            project_meta.get("release_batch_policy")
            or payload.get("release_batch_policy")
            or {}
        )
        if project_id and policy.get("sequential_lot_match_priority") is True:
            projects.add(project_id)
    return frozenset(projects)


def release_dispatch_rule_priority(
    project_id: str,
    batch_sequence: str | None,
) -> tuple[int, bool]:
    """Return ``(priority, derived)``; lower sequential lot goes first."""
    if str(project_id or "").strip() not in sequential_lot_priority_projects():
        return 100, False
    match = _LOT_RE.fullmatch(str(batch_sequence or "").strip())
    if not match:
        return 100, False
    lot_number = int(match.group(1))
    if lot_number <= 0:
        return 100, False
    suffix = (match.group(2) or "a").lower()
    return lot_number * 100 + (ord(suffix) - ord("a")), True
