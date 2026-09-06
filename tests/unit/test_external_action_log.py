"""Regression tests for the external-action claim boundary."""

from sop_hub.sop.external_action_log import (
    external_action_claimed,
    plan_external_action,
)


def test_only_created_claim_may_execute_external_send(tmp_path):
    """#issue-20260906: a duplicate claim must not reach the sender."""
    db_path = tmp_path / "sop.db"
    key = "chaoyang_steel:send_shipping_excel_wechat:eu_lot01_51"

    first = plan_external_action(
        db_path=db_path,
        project_id="chaoyang_steel",
        action_type="send_shipping_excel_wechat",
        idempotency_key=key,
        target_system="wechat",
    )
    replay = plan_external_action(
        db_path=db_path,
        project_id="chaoyang_steel",
        action_type="send_shipping_excel_wechat",
        idempotency_key=key,
        target_system="wechat",
    )

    assert first["action"] == "created"
    assert replay["action"] == "skipped"
    assert replay["reason"] == "duplicate_key"
    assert external_action_claimed(first) is True
    assert external_action_claimed(replay) is False
