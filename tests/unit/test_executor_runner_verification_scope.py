from sop_hub.sop.executor_runner import _verification_order_id


def test_jilin_factory_verification_prefers_batch_order_identifier():
    """反查必须按订单号收窄，不能空 orderId 扫描门户历史全量。"""
    assert _verification_order_id({
        "order_identifier": "CGR20260923082712",
        "order_id": "",
        "plan_id": "fallback-plan",
    }) == "CGR20260923082712"


def test_jilin_factory_verification_falls_back_only_when_order_identifier_absent():
    assert _verification_order_id({"plan_id": "fallback-plan"}) == "fallback-plan"
