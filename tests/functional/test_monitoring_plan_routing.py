"""监控计划路由(match_message_event)守门 —— fixture 化,不耦合 live SOP。

替代被删的 test_monitoring_plan_matcher(对真实编译计划断言、会随配置漂移)。
这里构造合成 monitoring_plan,只测路由逻辑本身:群定位 + 文本/图片命中 watch_item
+ fallback 锚点 + 多项目候选。2026-06-17。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop.monitoring_plan_matcher import MessageEvent, match_message_event

PLAN = {
    "wechat_monitoring_plan": {
        "GROUP001": {
            "group_name": "铁晟业务工作群",
            "watch_items": [
                {"input_type": "text", "message_type": "发车文本",
                 "text_patterns": ["四平铁", "马兰希望"],
                 "candidate_projects": ["jilin_jingang_jinzhou"],
                 "target_sop_nodes": {"jilin_jingang_jinzhou": ["departure_flow"]}},
                {"input_type": "image", "document_type": "出港计划通知单",
                 "candidate_projects": ["chaoyang_steel"],
                 "target_sop_nodes": {"chaoyang_steel": ["release_notice_flow"]}},
            ],
        },
    }
}


def _ev(group_id="GROUP001", text="", mtype="text", **md):
    return MessageEvent(message_id="m1", channel="wechat", group_id=group_id,
                        message_type=mtype, text=text, metadata=md)


def test_text_pattern_routes_to_project():
    r = match_message_event(_ev(text="煤一 四平铁 马兰希望 55车"), PLAN)
    assert r.matches and r.matches[0].candidate_projects == ["jilin_jingang_jinzhou"]
    assert r.matches[0].target_sop_nodes == {"jilin_jingang_jinzhou": ["departure_flow"]}


def test_image_document_type_routes():
    r = match_message_event(_ev(text="出港计划通知单", mtype="image"), PLAN)
    assert r.matches and r.matches[0].candidate_projects == ["chaoyang_steel"]


def test_group_resolved_by_name_substring():
    # group_id 不在计划里,但 metadata.group_name 含计划群名 → 仍定位到 GROUP001
    r = match_message_event(
        _ev(group_id="56327@chatroom", text="四平铁 马兰希望", group_name="铁晟业务工作群-[GROUP001]"),
        PLAN)
    assert r.matches and r.matches[0].group_id == "GROUP001"


def test_unknown_group_no_plan():
    r = match_message_event(_ev(group_id="GROUP999", text="四平铁"), PLAN)
    assert not r.matches
    assert "no monitoring plan" in r.reason


def test_no_anchor_no_match():
    r = match_message_event(_ev(text="收到,谢谢"), PLAN)
    assert not r.matches
    assert "no watch item matched" in r.reason


def test_fallback_alignment_when_no_watch_item():
    # "朝阳西" 不命中 GROUP001 的 watch_items(四平/出港通知单)→ 走 fallback → chaoyang
    r = match_message_event(_ev(text="朝阳西到货了"), PLAN)
    assert r.matches and r.matches[0].candidate_projects == ["chaoyang_steel"]
    assert "fallback" in r.matches[0].reason


def test_multiple_watch_items_can_match_text_and_image_separately():
    # 同群两类输入各走各的 watch_item(路由是 per-message-type 的)
    txt = match_message_event(_ev(text="马兰希望", mtype="text"), PLAN)
    img = match_message_event(_ev(text="出港计划通知单", mtype="image"), PLAN)
    assert txt.matches[0].candidate_projects == ["jilin_jingang_jinzhou"]
    assert img.matches[0].candidate_projects == ["chaoyang_steel"]
