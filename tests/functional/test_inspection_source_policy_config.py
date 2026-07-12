from __future__ import annotations

from pathlib import Path

from sop_hub.models.project_sop import load_project_sop


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "project_sops"


def _inspection_routes(sop_name: str, group_id: str) -> list[tuple[str, str]]:
    sop = load_project_sop(CONFIG_DIR / sop_name)
    task = next(t for t in sop.listening_tasks if t.group_id == group_id)
    return [
        (route.message_type, route.trigger_condition)
        for route in task.routing
        if "检装车通知单" in route.trigger_condition
    ]


def test_zhongtang_only_group001_routes_inspection_images():
    assert _inspection_routes("zhongtang.yaml", "GROUP001") == [
        ("image", "category_in:[检装车通知单]")
    ]
    assert _inspection_routes("zhongtang.yaml", "GROUP003") == []
    assert _inspection_routes("zhongtang.yaml", "GROUP013") == []


def test_chaoyang_only_group001_routes_inspection_images():
    assert _inspection_routes("chaoyang.yaml", "GROUP001") == [
        ("image", "category_in:[检装车通知单]")
    ]
    assert _inspection_routes("chaoyang.yaml", "GROUP013") == []
