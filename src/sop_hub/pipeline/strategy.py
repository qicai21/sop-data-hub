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
    routes = {
        "出港计划通知单": CategoryRoute("出港计划通知单", bucket="table", action="departure_plan_extract", output_subdir="出港计划通知单"),
        "耗材统计表": CategoryRoute("耗材统计表", bucket="table", action="materials_extract", output_subdir="耗材统计表"),
        "检装车通知单": CategoryRoute("检装车通知单", bucket="table", action="inspection_slip_extract", output_subdir="检装车通知单"),
        "请车表": CategoryRoute("请车表", bucket="table", action="save_only", output_subdir="请车表"),
        "日现场工作记录表": CategoryRoute("日现场工作记录表", bucket="table", action="save_only", output_subdir="日现场工作记录表"),
        "手写箱号车号表": CategoryRoute("手写箱号车号表", bucket="handwritten", action="save_only", output_subdir="手写箱号车号表"),
        "手写记录": CategoryRoute("手写记录", bucket="handwritten", action="save_only", output_subdir="手写记录"),
        "照片-敞车内部情况和作业": CategoryRoute("照片-敞车内部情况和作业", bucket="photo", action="save_only", output_subdir="照片-敞车内部情况和作业"),
        "照片-火车涂写mark": CategoryRoute("照片-火车涂写mark", bucket="photo", action="save_only", output_subdir="照片-火车涂写mark"),
        "照片-货垛": CategoryRoute("照片-货垛", bucket="photo", action="save_only", output_subdir="照片-货垛"),
        "照片-集装箱内情况和作业": CategoryRoute("照片-集装箱内情况和作业", bucket="photo", action="save_only", output_subdir="照片-集装箱内情况和作业"),
        "照片-检查工人": CategoryRoute("照片-检查工人", bucket="photo", action="save_only", output_subdir="照片-检查工人"),
        "照片-装卸现场情况": CategoryRoute("照片-装卸现场情况", bucket="photo", action="save_only", output_subdir="照片-装卸现场情况"),
        "照片-杂物垃圾-塑料布": CategoryRoute("照片-杂物垃圾-塑料布", bucket="photo", action="save_only", output_subdir="照片-杂物垃圾-塑料布"),
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
