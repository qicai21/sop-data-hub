"""Functional tests for freight detail extractor — R40."""

from sop_hub.sop.freight_detail_extractor import (
    FreightDetailCandidate,
    extract_freight_detail,
)
from sop_hub.sop.monitoring_plan_matcher import MessageEvent


# ── Complete extractions ───────────────────────────────────────────────

def test_jinzhou_yinfen_3000t():
    """锦州港放货印粉3000吨 — complete extraction"""
    c = extract_freight_detail(
        "锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954"
    )
    assert c.status == "complete"
    assert c.order_identifier == "CGR20260520095954"
    assert c.contract_no == "HNMC20260520-1X-1"
    assert c.cargo_name_detail == "印粉"
    assert c.quantity_tons == 3000
    assert c.port == "锦州港"
    assert c.binding_status == "needs_manual_binding"
    assert c.project_id == "jilin_jingang_jinzhou"


def test_bayuquan_mbfen_2000t():
    """鲅鱼圈港放货mb粉2000吨 — different port, cargo, contract pattern"""
    c = extract_freight_detail(
        "鲅鱼圈港今日放货mb粉2000吨入厂合同号XYSJLJR25KF1222-01-1，订单标识CGR20251222174305"
    )
    assert c.status == "complete"
    assert c.order_identifier == "CGR20251222174305"
    assert c.contract_no == "XYSJLJR25KF1222-01-1"
    assert c.cargo_name_detail == "mb粉"
    assert c.quantity_tons == 2000
    assert c.port == "鲅鱼圈港"
    assert c.binding_status == "needs_manual_binding"


# ── No order identifier → no_match ────────────────────────────────────

def test_no_order_identifier_no_match():
    """Text without '订单标识' → no_match"""
    c = extract_freight_detail("锦州港今日放货印粉3000吨, 合同号HNMC20260520-1X-1")
    assert c.status == "no_match"
    assert c.order_identifier == ""


# ── Departure text not misidentified ───────────────────────────────────

def test_departure_text_not_matched():
    """'6道，四平铁，46车' should NOT be caught as freight detail"""
    c = extract_freight_detail("6道，四平铁，46车")
    assert c.status == "no_match"


def test_departure_text_with_ship_not_matched():
    """'十四道 41节 四平铁 厦门世纪' should NOT be caught"""
    c = extract_freight_detail("十四道 41节 四平铁 厦门世纪")
    assert c.status == "no_match"


# ── binding_status = needs_manual_binding ──────────────────────────────

def test_binding_status_always_needs_manual():
    """Freight detail candidates always need manual binding (no ship name)"""
    c = extract_freight_detail(
        "锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954"
    )
    assert c.binding_status == "needs_manual_binding"


# ── Incomplete: has order_identifier but missing other fields ──────────

def test_only_order_identifier_incomplete():
    """Only '订单标识CGRxxx' with no contract/cargo → incomplete"""
    c = extract_freight_detail("订单标识CGR20260601000001")
    assert c.status == "incomplete"
    assert c.order_identifier == "CGR20260601000001"
    assert c.contract_no == ""


# ── MessageEvent integration ──────────────────────────────────────────

def test_from_message_event():
    """Extractor accepts MessageEvent directly."""
    event = MessageEvent(
        message_id="wx_002",
        channel="wechat",
        group_id="GROUP005",
        message_type="text",
        received_at="2026-05-28T09:00:00Z",
        text="锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954",
    )
    c = extract_freight_detail(event)
    assert c.status == "complete"
    assert c.message_id == "wx_002"
    assert c.group_id == "GROUP005"
    assert c.message_time == "2026-05-28T09:00:00Z"
    assert c.order_identifier == "CGR20260520095954"


# ── to_dict() ─────────────────────────────────────────────────────────

def test_to_dict():
    c = extract_freight_detail(
        "锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954"
    )
    d = c.to_dict()
    assert d["order_identifier"] == "CGR20260520095954"
    assert d["contract_no"] == "HNMC20260520-1X-1"
    assert d["status"] == "complete"
    assert d["binding_status"] == "needs_manual_binding"
    assert d["source"] == "freight_detail_extractor"


# ── Empty text → no_match ─────────────────────────────────────────────

def test_empty_text_no_match():
    c = extract_freight_detail("")
    assert c.status == "no_match"
