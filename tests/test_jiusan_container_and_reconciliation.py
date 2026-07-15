from __future__ import annotations

from sop_hub.fees.jiusan_container import calc_route_a_fee_items, container_billing_weight, load_route_a_config
from sop_hub.fees.jiusan_container_types import OPEN_TOP, TOP_OPEN, UNKNOWN_TYPE, classify_container_type
from sop_hub.fees.reconciliation import group_ship_fee_rows


def test_container_billing_weight():
    assert container_billing_weight(630, 28.4) == 17892.0


def test_route_a_metro_fee_uses_marked_weight_not_box_count():
    item = next(item for item in load_route_a_config()["items"] if item["code"] == "route_a_metro_fee")
    assert item["base"] == "marked_weight"
    assert item["rate"] == 3.75


def test_jiusan_container_type_ranges():
    assert classify_container_type("TBCU0405966") == OPEN_TOP
    assert classify_container_type("TBJU5939999") == OPEN_TOP
    assert classify_container_type("TBJU5940000") == TOP_OPEN
    assert classify_container_type("TBJU6209999") == TOP_OPEN
    assert classify_container_type("TBJU6210000") == OPEN_TOP
    assert classify_container_type("OTHER1234567") == UNKNOWN_TYPE


def test_calc_route_a_fee_items():
    terms = {
        "route_a_income": {"fee_name": "路线A运输收入", "charge_side": "income", "pricing_basis": "confirmed_weight", "default_rate": 65.43, "counterparty": "物流发展"},
        "route_a_nrf_cost": {"fee_name": "路线A国铁运费", "charge_side": "cost", "pricing_basis": "freight_fee_sum", "settle_party": "中国铁路"},
        "route_a_metro_fee": {"fee_name": "路线A地铁费", "charge_side": "cost", "pricing_basis": "marked_weight_x_line_rate", "default_rate": 3.75, "settle_party": "高天"},
        "route_a_wagon_occupancy": {"fee_name": "路线A货车占用费", "charge_side": "cost", "pricing_basis": "marked_weight_tiered", "default_rate": 0.64, "settle_party": "高天"},
        "route_a_transfer_fee": {"fee_name": "路线A倒运费", "charge_side": "cost", "pricing_basis": "box_trip_count", "default_rate": 444.0, "settle_party": "诚信"},
        "route_a_tarpaulin": {"fee_name": "路线A篷布", "charge_side": "cost", "pricing_basis": "formula", "default_rate": 12.8, "settle_party": "物流发展"},
        "route_a_item9": {"fee_name": "路线A第9项", "charge_side": "cost", "pricing_basis": "box_trip_count", "default_rate": 40.0, "settle_party": "二级公司"},
        "route_a_item10": {"fee_name": "路线A第10项", "charge_side": "cost", "pricing_basis": "box_trip_count", "default_rate": 100.0, "settle_party": "二级公司"},
        "route_a_item11": {"fee_name": "路线A第11项", "charge_side": "cost", "pricing_basis": "box_trip_count", "default_rate": 80.0, "settle_party": "二级公司"},
        "route_a_item13": {"fee_name": "路线A第13项", "charge_side": "cost", "pricing_basis": "open_top_box_trip_count", "default_rate": 15.0, "settle_party": "二级公司"},
        "route_a_item18": {"fee_name": "路线A第18项", "charge_side": "cost", "pricing_basis": "box_trip_count", "default_rate": 15.0, "settle_party": "二级公司"},
    }
    items = calc_route_a_fee_items(
        terms=terms,
        box_trip_count=1709,
        confirmed_weight=47951.06,
        railway_weight=54688.0,
        freight_sum_yuan=1619604.0,
        metro_amount=205080.0,
        wagon_occupancy_amount=35000.32,
        transfer_amount=758796.0,
        tarpaulin_amount=15312.64,
        item9_amount=68360.0,
        item10_amount=170900.0,
        item11_amount=136720.0,
        item13_amount=19200.0,
        item13_box_count=1280,
        item18_amount=25635.0,
        source_ref="test",
    )
    by_code = {item.code: item for item in items}
    assert by_code["route_a_income"].amount == round(47951.06 * 65.43, 2)
    assert by_code["route_a_nrf_cost"].amount == 1619604.0
    assert by_code["route_a_nrf_cost"].qty == 54688.0
    assert by_code["route_a_metro_fee"].amount == 205080.0
    assert by_code["route_a_transfer_fee"].amount == 758796.0
    assert by_code["route_a_wagon_occupancy"].amount == 35000.32
    assert by_code["route_a_tarpaulin"].amount == 15312.64
    assert by_code["route_a_item9"].qty == 1709
    assert by_code["route_a_item10"].amount == 170900.0
    assert by_code["route_a_item11"].amount == 136720.0
    assert by_code["route_a_item13"].qty == 1280
    assert by_code["route_a_item13"].pricing_basis == "open_top_box_trip_count"
    assert by_code["route_a_item13"].amount == 19200.0
    assert by_code["route_a_item18"].amount == 25635.0


def test_group_ship_fee_rows_dedup_batches():
    groups = group_ship_fee_rows(
        [
            {
                "fee_batch_id": "b1",
                "fee_code": "route_a_income",
                "charge_side": "income",
                "counterparty": "物流发展",
                "settle_party": "",
                "amount": 100.0,
                "wagon_count": 10,
                "container_count": 20,
                "total_weight": 500.0,
            },
            {
                "fee_batch_id": "b1",
                "fee_code": "route_a_other",
                "charge_side": "income",
                "counterparty": "物流发展",
                "settle_party": "",
                "amount": 50.0,
                "wagon_count": 10,
                "container_count": 20,
                "total_weight": 500.0,
            },
            {
                "fee_batch_id": "b2",
                "fee_code": "route_c_income",
                "charge_side": "income",
                "counterparty": "物流发展",
                "settle_party": "",
                "amount": 80.0,
                "wagon_count": 5,
                "container_count": 0,
                "total_weight": 300.0,
            },
        ]
    )
    assert len(groups) == 1
    group = groups[0]
    assert group.total_amount == 230.0
    assert group.wagon_count == 15
    assert group.container_count == 20
    assert group.total_weight == 800.0
    assert group.fee_codes == ["route_a_income", "route_a_other", "route_c_income"]
