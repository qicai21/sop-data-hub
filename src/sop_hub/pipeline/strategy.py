from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

from sop_hub.pipeline.models import CategoryRoute, GroupImageStrategy


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "business_group_image_strategies.yaml"


def _default_strategy() -> GroupImageStrategy:
    # 2026-06 收敛为 4 类:仅"检装车通知单"和"出港计划通知单"触发抽取,
    # 其余单据归"其他业务图片",照片归"现场作业照片",均 save_only。
    routes = {
        "检装车通知单": CategoryRoute("检装车通知单", bucket="table", action="inspection_slip_extract", output_subdir="检装车通知单"),
        "出港计划通知单": CategoryRoute("出港计划通知单", bucket="table", action="departure_plan_extract", output_subdir="出港计划通知单"),
        "其他业务图片": CategoryRoute("其他业务图片", bucket="other", action="save_only", output_subdir="其他业务图片"),
        "现场作业照片": CategoryRoute("现场作业照片", bucket="photo", action="save_only", output_subdir="现场作业照片"),
        "other": CategoryRoute("other", bucket="other", action="save_only", output_subdir="other"),
    }
    return GroupImageStrategy(
        name="铁晟业务工作群",
        wxid="18919596289@chatroom",
        enabled=True,
        routes=routes,
    )


def _parse_strategy(item: Dict[str, Any]) -> GroupImageStrategy:
    routes: dict[str, CategoryRoute] = {}
    for category, route_data in (item.get("routes") or {}).items():
        routes[category] = CategoryRoute(
            category=category,
            bucket=str(route_data.get("bucket", "other")),
            action=str(route_data.get("action", "save_only")),
            output_subdir=str(route_data.get("output_subdir", category)),
            enabled=bool(route_data.get("enabled", True)),
            notes=[str(x) for x in (route_data.get("notes") or [])],
        )
    return GroupImageStrategy(
        name=str(item.get("name", "")),
        wxid=str(item.get("wxid", "")),
        enabled=bool(item.get("enabled", True)),
        classifier_prompt_version=str(item.get("classifier_prompt_version", "v1")),
        fallback_category=str(item.get("fallback_category", "other")),
        routes=routes,
    )


def load_business_group_strategies(config_path: str | Path | None = None) -> dict[str, GroupImageStrategy]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        strategy = _default_strategy()
        return {strategy.wxid: strategy}
    raw = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw) or {}
    groups = data.get("groups") or []
    strategies: dict[str, GroupImageStrategy] = {}
    for item in groups:
        if not isinstance(item, dict):
            continue
        strategy = _parse_strategy(item)
        if strategy.wxid:
            strategies[strategy.wxid] = strategy
    if not strategies:
        strategy = _default_strategy()
        strategies[strategy.wxid] = strategy
    return strategies
