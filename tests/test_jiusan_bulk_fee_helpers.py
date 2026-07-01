from pathlib import Path

from sop_hub.fees.jiusan_bulk import (
    billing_weight,
    build_docx_path,
    calc_fee_items,
    display_track,
    freight_fee_yuan,
    is_real_track,
)


def test_track_detection() -> None:
    assert is_real_track("七道")
    assert is_real_track("煤六")
    assert not is_real_track("诚信")
    assert display_track("诚信") == "待补录"
    assert display_track("七道") == "七道"


def test_freight_fee_yuan() -> None:
    assert freight_fee_yuan(294530) == 2945.30
    assert freight_fee_yuan(None) == 0.0


def test_billing_weight_prefers_computed_loading_weight() -> None:
    assert billing_weight({"computed_loading_weight": 69, "car_model": "L18"}) == 69.0
    assert billing_weight({"computed_loading_weight": None, "car_model": "L70"}) == 69.0
    assert billing_weight({"computed_loading_weight": None, "car_model": "L18"}) == 61.0


def test_calc_fee_items_route_c() -> None:
    cfg = {
        "items": [
            {"code": "route_c_income", "name": "收入", "side": "income", "rate": 65.13, "base": "railway_billing_weight", "tax": 0.09, "counterparty": "甲"},
            {"code": "nrf_cost", "name": "国铁", "side": "cost", "calc": "actual_freight_sum", "tax": 0.09, "settle_party": "国铁"},
            {"code": "aux_bulk_loading", "name": "辅助", "side": "cost", "rate": 450.0, "base": "car_count", "tax": 0.06, "settle_party": "二级公司", "doc_type": "onsite_confirm_sheet", "service_no": "19"},
        ]
    }
    items = calc_fee_items(config=cfg, total_weight=690.0, freight_sum_yuan=33821.0, car_count=10, source_ref="x")
    assert [i.code for i in items] == ["route_c_income", "nrf_cost", "aux_bulk_loading"]
    assert items[0].amount == 44939.7
    assert items[1].amount == 33821.0
    assert round(items[1].price or 0, 4) == round(33821.0 / 690.0, 4)
    assert items[2].amount == 4500.0


def test_build_docx_path() -> None:
    path = build_docx_path("2026-06-26", "诚信", "七道", 50)
    assert isinstance(path, Path)
    assert "2026-06" in str(path)
    assert path.name.endswith(".docx")
