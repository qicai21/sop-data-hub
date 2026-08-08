"""#issue-20260619:出港/检装车通知单 → SOP 项目识别(_infer_sop_project_token)。

根因:九三大豆(诚信/和谐1→新台子)此前漏了 jiusan 分支,通知单一律
no_sop_project_match 卡 _pending、无法归档/落库。本测试守住四项目都能识别。
"""
from __future__ import annotations

import sop_hub.runner as r


def _payload(ship: str, dest: str, cargo: str = "") -> dict:
    return {
        "business_info": {"船名": ship, "进口船名": ship},
        "cargo_info": {"货物名称": cargo},
        "special_matter": f"到站：{dest}",
        "is_target": True,
    }


def test_jiusan_departure_inferred():
    for ship in ("诚信", "和谐1", "昆娜", "玛格丽特"):
        p = _payload(ship, "新台子", "大豆")
        assert r._infer_sop_project_token(p, category="出港计划通知单") == "jiusan"
        assert r._ensure_sop_project(dict(p), category="出港计划通知单") is True


def test_other_projects_still_inferred():
    cases = {
        "朝阳西": "chaoyang_steel",
        "汐子": "zhongtang_special_steel",
        "四平": "jilin_jingang_jinzhou",
    }
    for dest, expect in cases.items():
        p = _payload("某船", dest)
        assert r._infer_sop_project_token(p, category="出港计划通知单") == expect


def test_unknown_destination_no_match():
    p = _payload("某船", "不存在的站")
    assert r._infer_sop_project_token(p, category="出港计划通知单") == ""


def test_jiusan_inspection_inferred():
    p = _payload("和谐1", "新台子")
    assert r._infer_sop_project_token(p, category="检装车通知单") == "jiusan"
