"""Functional test for SOP Monitoring Plan Compiler.

This test captures the intended business behavior before implementation:
multiple project SOPs must be compiled into channel/group-level monitoring
plans, deduplicating watch items while preserving candidate project and target
SOP node mappings.
"""

import pytest

from ops_hub.sop.monitoring_plan_compiler import SopMonitoringPlanCompiler


def find_watch_item(items, **criteria):
    for item in items:
        if all(item.get(key) == value for key, value in criteria.items()):
            return item
    raise AssertionError(f"watch item not found: {criteria}")


def has_watch_item(items, **criteria):
    try:
        find_watch_item(items, **criteria)
        return True
    except AssertionError:
        return False


def all_candidate_projects(group_plan):
    projects = set()
    for item in group_plan["watch_items"]:
        projects.update(item.get("candidate_projects", []))
    return projects


def test_sop_monitoring_plan_compiler_functional():
    project_sops = [
        {
            "project_id": "zhongtang_special_steel",
            "project_name": "中唐特钢",
            "sop_nodes": [
                {
                    "node_id": "departure_plan_notice",
                    "node_name": "出港计划通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        }
                    ],
                },
                {
                    "node_id": "inspection_loading_notice",
                    "node_name": "检装车通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "检装车通知单",
                        }
                    ],
                },
            ],
        },
        {
            "project_id": "chaoyang_steel",
            "project_name": "朝阳钢铁",
            "sop_nodes": [
                {
                    "node_id": "departure_plan_notice",
                    "node_name": "出港计划通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        },
                        {
                            "channel": "wechat",
                            "group_id": "group_3",
                            "group_name": "微信3号群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        },
                    ],
                },
                {
                    "node_id": "inspection_loading_notice",
                    "node_name": "检装车通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "检装车通知单",
                        }
                    ],
                },
                {
                    "node_id": "text_release_message",
                    "node_name": "文字放货消息",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_3",
                            "group_name": "微信3号群",
                            "input_type": "text",
                            "message_type": "文字放货消息",
                            "text_patterns": ["放货", "发运", "到港", "卸船"],
                        }
                    ],
                },
            ],
        },
        {
            "project_id": "jiusan",
            "project_name": "九三",
            "sop_nodes": [
                {
                    "node_id": "soybean_arrival_notice",
                    "node_name": "大豆到站通知",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_2",
                            "group_name": "微信2号群",
                            "input_type": "text",
                            "message_type": "到站消息",
                            "text_patterns": ["到站", "新台子", "锦州港"],
                        }
                    ],
                }
            ],
        },
    ]

    compiler = SopMonitoringPlanCompiler()
    plan = compiler.compile(project_sops)

    assert "wechat_monitoring_plan" in plan

    wechat_plan = plan["wechat_monitoring_plan"]
    assert set(wechat_plan.keys()) == {"group_1", "group_2", "group_3"}

    group_1_items = wechat_plan["group_1"]["watch_items"]
    assert len(group_1_items) == 2

    departure_item = find_watch_item(
        group_1_items,
        input_type="document",
        document_type="出港计划通知单",
    )
    assert set(departure_item["candidate_projects"]) == {
        "zhongtang_special_steel",
        "chaoyang_steel",
    }
    assert departure_item["target_sop_nodes"] == {
        "zhongtang_special_steel": ["departure_plan_notice"],
        "chaoyang_steel": ["departure_plan_notice"],
    }

    inspection_item = find_watch_item(
        group_1_items,
        input_type="document",
        document_type="检装车通知单",
    )
    assert set(inspection_item["candidate_projects"]) == {
        "zhongtang_special_steel",
        "chaoyang_steel",
    }
    assert inspection_item["target_sop_nodes"] == {
        "zhongtang_special_steel": ["inspection_loading_notice"],
        "chaoyang_steel": ["inspection_loading_notice"],
    }

    group_3_items = wechat_plan["group_3"]["watch_items"]
    assert has_watch_item(
        group_3_items,
        input_type="document",
        document_type="出港计划通知单",
    )
    assert has_watch_item(
        group_3_items,
        input_type="text",
        message_type="文字放货消息",
    )

    for item in group_3_items:
        assert item["candidate_projects"] == ["chaoyang_steel"]

    assert "jiusan" not in all_candidate_projects(wechat_plan["group_1"])
    assert "jiusan" not in all_candidate_projects(wechat_plan["group_3"])
    assert "jiusan" in all_candidate_projects(wechat_plan["group_2"])

    for group_plan in wechat_plan.values():
        for item in group_plan["watch_items"]:
            assert item.get("target_sop_nodes")


def test_sop_monitoring_plan_compiler_returns_empty_wechat_plan_for_empty_input():
    assert SopMonitoringPlanCompiler().compile([]) == {"wechat_monitoring_plan": {}}


@pytest.mark.parametrize(
    "project_sops",
    [
        [
            {
                "project_id": "sample_project",
                "project_name": "示例项目",
                "sop_nodes": [
                    {
                        "node_id": "email_notice",
                        "node_name": "邮件通知",
                        "monitoring": [
                            {
                                "channel": "email",
                                "group_id": "email_group",
                                "group_name": "邮件组",
                                "input_type": "document",
                                "document_type": "邮件通知单",
                            },
                            {
                                "channel": "rail95306",
                                "group_id": "rail_group",
                                "group_name": "95306组",
                                "input_type": "text",
                                "message_type": "铁路消息",
                            },
                        ],
                    }
                ],
            }
        ]
    ],
)
def test_sop_monitoring_plan_compiler_skips_non_wechat_channels(project_sops):
    assert SopMonitoringPlanCompiler().compile(project_sops) == {"wechat_monitoring_plan": {}}


@pytest.mark.parametrize(
    "project_sops",
    [
        [
            {
                "project_id": "sample_project",
                "project_name": "示例项目",
                "sop_nodes": [
                    {
                        "node_id": "unbound_notice",
                        "node_name": "无群通知",
                        "monitoring": [
                            {
                                "channel": "wechat",
                                "input_type": "document",
                                "document_type": "无群通知单",
                            }
                        ],
                    }
                ],
            }
        ]
    ],
)
def test_sop_monitoring_plan_compiler_skips_wechat_requirements_without_group_id(project_sops):
    assert SopMonitoringPlanCompiler().compile(project_sops) == {"wechat_monitoring_plan": {}}


def test_sop_monitoring_plan_compiler_deduplicates_text_patterns_in_first_seen_order():
    project_sops = [
        {
            "project_id": "chaoyang_steel",
            "project_name": "朝阳钢铁",
            "sop_nodes": [
                {
                    "node_id": "text_release_message",
                    "node_name": "文字放货消息",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_3",
                            "group_name": "微信3号群",
                            "input_type": "text",
                            "message_type": "文字放货消息",
                            "text_patterns": ["放货", "发运", "放货", "到港"],
                        }
                    ],
                }
            ],
        }
    ]

    plan = SopMonitoringPlanCompiler().compile(project_sops)
    group_3_items = plan["wechat_monitoring_plan"]["group_3"]["watch_items"]
    text_item = find_watch_item(group_3_items, input_type="text", message_type="文字放货消息")

    assert text_item["text_patterns"] == ["放货", "发运", "到港"]


def test_sop_monitoring_plan_compiler_deduplicates_target_sop_nodes_per_project_and_node():
    project_sops = [
        {
            "project_id": "chaoyang_steel",
            "project_name": "朝阳钢铁",
            "sop_nodes": [
                {
                    "node_id": "departure_plan_notice",
                    "node_name": "出港计划通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        }
                    ],
                },
                {
                    "node_id": "departure_plan_notice",
                    "node_name": "出港计划通知单",
                    "monitoring": [
                        {
                            "channel": "wechat",
                            "group_id": "group_1",
                            "group_name": "微信1号群",
                            "input_type": "document",
                            "document_type": "出港计划通知单",
                        }
                    ],
                },
            ],
        }
    ]

    plan = SopMonitoringPlanCompiler().compile(project_sops)
    group_1_items = plan["wechat_monitoring_plan"]["group_1"]["watch_items"]
    departure_item = find_watch_item(
        group_1_items,
        input_type="document",
        document_type="出港计划通知单",
    )

    assert departure_item["target_sop_nodes"] == {
        "chaoyang_steel": ["departure_plan_notice"]
    }


def test_sop_monitoring_plan_compiler_import_contract_exists():
    """Compiler class should become a stable public import contract."""
    assert SopMonitoringPlanCompiler is not None
