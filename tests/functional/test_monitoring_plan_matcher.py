"""Functional tests for the monitoring plan message matcher.

Scope:
- 中唐特钢
- 朝阳钢铁
- 吉林金钢 / 吉林金刚

No runtime, wx-ops-agent, database, 95306, or report sending.
"""

from pathlib import Path

from sop_hub.sop.monitoring_plan_matcher import MessageEvent, match_message_event
from sop_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sops"


def _plan():
    return build_real_sop_monitoring_plan_preview(FIXTURE_DIR).plan


def _first_match(result):
    assert result.matches, result.reason
    return result.matches[0]


def test_matcher_matches_group001_express_notice_to_three_projects():
    result = match_message_event(
        MessageEvent(
            message_id="msg-a",
            channel="wechat",
            group_id="GROUP001",
            message_type="image",
            text="出港计划通知单",
        ),
        _plan(),
    )

    match = _first_match(result)
    assert not result.reason
    assert match.group_id == "GROUP001"
    assert match.watch_item["document_type"] == "出港计划通知单"
    assert set(match.candidate_projects) == {
        "zhongtang_special_steel",
        "chaoyang_steel",
        "jilin_jingang_jinzhou",
    }
    assert match.target_sop_nodes["zhongtang_special_steel"]
    assert match.target_sop_nodes["chaoyang_steel"]
    assert match.target_sop_nodes["jilin_jingang_jinzhou"]


def test_matcher_matches_group001_loading_notice_without_jiusan():
    result = match_message_event(
        MessageEvent(
            message_id="msg-b",
            channel="wechat",
            group_id="GROUP001",
            message_type="image",
            text="检装车通知单",
        ),
        _plan(),
    )

    match = _first_match(result)
    assert not result.reason
    assert match.group_id == "GROUP001"
    assert match.watch_item["document_type"] == "检装车通知单"
    assert set(match.candidate_projects) == {"zhongtang_special_steel", "chaoyang_steel"}
    assert "jilin_jingang_jinzhou" not in match.candidate_projects
    assert match.target_sop_nodes["zhongtang_special_steel"]
    assert match.target_sop_nodes["chaoyang_steel"]


def test_matcher_returns_no_match_for_unknown_group_or_unmatched_text():
    result = match_message_event(
        MessageEvent(
            message_id="msg-c",
            channel="wechat",
            group_id="GROUP999",
            message_type="text",
            text="出港计划通知单",
        ),
        _plan(),
    )

    assert not result.matches
    assert "GROUP999" in result.reason
    assert "no monitoring plan" in result.reason
