from __future__ import annotations

from sop_hub.fees.jiusan_container import calc_route_a_fee_items, container_billing_weight
from sop_hub.fees.reconciliation import group_ship_fee_rows


def test_container_billing_weight():
    assert container_billing_weight(630, 28.4) == 17892.0


def test_calc_route_a_fee_items():
    cfg = {
        "items": [
            {"code": "route_a_income", "name": "路线A运输收入", "side": "income", "rate": 65.43, "base": "railway_billing_weight", "counterparty": "物流发展"},
            {"code": "route_a_nrf_cost", "name": "路线A国铁运费", "side": "cost", "calc": "actual_freight_sum", "settle_party": "中国铁路"},
            {"code": "route_a_metro_fee", "name": "路线A地铁费", "side": "cost", "rate": 120.0, "base": "box_count", "settle_party": "高天"},
            {"code": "route_a_transfer_fee", "name": "路线A倒运费", "side": "cost", "rate": 444.0, "base": "box_count", "settle_party": "诚信"},
        ]
    }
    items = calc_route_a_fee_items(
        config=cfg,
        box_count=1709,
        total_weight=17892.0,
        railway_weight=54688.0,
        freight_sum_yuan=1619604.0,
        source_ref="test",
    )
    by_code = {item.code: item for item in items}
    assert by_code["route_a_income"].amount == 1170673.56
    assert by_code["route_a_nrf_cost"].amount == 1619604.0
    assert by_code["route_a_nrf_cost"].qty == 54688.0
    assert by_code["route_a_metro_fee"].amount == 205080.0
    assert by_code["route_a_transfer_fee"].amount == 758796.0


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
