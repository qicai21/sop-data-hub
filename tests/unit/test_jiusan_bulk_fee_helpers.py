from pathlib import Path

from sop_hub.fees.jiusan_bulk import (
    allocated_confirmed_weight,
    billing_weight,
    build_docx_path,
    calc_fee_items,
    display_track,
    freight_fee_yuan,
    is_real_track,
    resolve_line_rate,
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


def test_billing_weight_prefers_marked_weight() -> None:
    assert billing_weight({"marked_weight": 68.7, "computed_loading_weight": 69, "car_model": "L18"}) == 68.7
    assert billing_weight({"computed_loading_weight": None, "car_model": "L70"}) == 69.0
    assert billing_weight({"computed_loading_weight": None, "car_model": "L18"}) == 61.0


def test_calc_fee_items_route_c() -> None:
    terms = {
        "route_c_income": {"fee_name": "收入", "charge_side": "income", "pricing_basis": "confirmed_weight", "default_rate": 65.13, "tax_rate": 0.09, "counterparty": "甲"},
        "nrf_cost": {"fee_name": "国铁", "charge_side": "cost", "pricing_basis": "freight_fee_sum", "tax_rate": 0.09, "settle_party": "国铁"},
        "aux_bulk_loading": {"fee_name": "辅助", "charge_side": "cost", "pricing_basis": "car_count", "default_rate": 450.0, "tax_rate": 0.06, "settle_party": "二级公司"},
        "metro_fee": {"fee_name": "地铁费", "charge_side": "cost", "pricing_basis": "marked_weight_x_line_rate", "tax_rate": 0.09},
    }
    items = calc_fee_items(terms=terms, total_weight=690.0, freight_sum_yuan=33821.0, car_count=10, line_rate=3.75, source_ref="x")
    assert [i.code for i in items] == ["route_c_income", "nrf_cost", "aux_bulk_loading", "metro_fee"]
    assert items[0].amount == 44939.7
    assert items[1].amount == 33821.0
    assert round(items[1].price or 0, 4) == round(33821.0 / 690.0, 4)
    assert items[2].amount == 4500.0
    assert items[3].amount == 2587.5
    assert items[2].service_no == "19"

def test_calc_fee_items_route_c_income_can_use_confirmed_override() -> None:
    terms = {
        "route_c_income": {"fee_name": "收入", "charge_side": "income", "pricing_basis": "confirmed_weight", "default_rate": 65.13},
        "metro_fee": {"fee_name": "地铁费", "charge_side": "cost", "pricing_basis": "marked_weight_x_line_rate"},
    }
    items = calc_fee_items(
        terms=terms,
        total_weight=690.0,
        freight_sum_yuan=0.0,
        car_count=10,
        line_rate=3.75,
        code_weights={"route_c_income": 688.2},
        source_ref="x",
    )
    assert items[0].qty == 688.2
    assert items[0].pricing_basis_value == 688.2
    assert items[0].amount == round(688.2 * 65.13, 2)
    assert items[1].qty == 690.0


def test_allocated_confirmed_weight() -> None:
    assert allocated_confirmed_weight(20112.7, 3138.0, 20344.0) == round(20112.7 * 3138.0 / 20344.0, 2)


def test_resolve_line_rate() -> None:
    assert resolve_line_rate("七道", {"七道": 3.75}, 4.5) == (3.75, "七道")
    assert resolve_line_rate("", {"七道": 3.75}, 4.5) == (4.5, "")


def test_build_docx_path() -> None:
    path = build_docx_path("2026-06-26", "诚信", "七道", 50)
    assert isinstance(path, Path)
    assert "2026-06" in str(path)
    assert path.name.endswith(".docx")
