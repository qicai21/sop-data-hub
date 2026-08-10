from __future__ import annotations

import requests

from sop_hub.sop import factory_edit


def _row(**overrides):
    row = {
        "id": 279903,
        "wagonNumber": "5248160",
        "boxNumber": "TBJU1369339",
        "orderId": "OLD",
        "contractNumber": "CONTRACT-A",
        "isAppointment": 0,
        "recordDate": "2026-08-10 09:46:03",
        "params": {},
        "creator": "saibin",
    }
    row.update(overrides)
    return row


def test_dry_run_preserves_full_portal_payload(monkeypatch):
    before = _row()
    monkeypatch.setattr(factory_edit, "_login", lambda: ("token", ""))
    monkeypatch.setattr(
        factory_edit, "fetch_full_order_rows", lambda *args, **kwargs: [before]
    )

    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"orderId": "NEW"},
        expected_portal_id=279903,
    )

    assert not result.error
    assert not result.applied
    assert result.after["orderId"] == "NEW"
    assert result.after["creator"] == "saibin"
    assert result.after["params"] == {}


def test_apply_verifies_old_disappears_and_new_is_unique(monkeypatch):
    before = _row()
    after = _row(orderId="NEW")
    calls = {"OLD": 0, "NEW": 0}

    def fetch(order_id, *args, **kwargs):
        calls[order_id] += 1
        if order_id == "OLD":
            return [before] if calls[order_id] == 1 else []
        return [after]

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

    posted = {}

    def post(url, *, json, **kwargs):
        posted["url"] = url
        posted["json"] = json
        return Response()

    monkeypatch.setattr(factory_edit, "_login", lambda: ("token", ""))
    monkeypatch.setattr(factory_edit, "fetch_full_order_rows", fetch)
    monkeypatch.setattr(factory_edit.requests, "post", post)

    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"orderId": "NEW"},
        expected_portal_id=279903,
        dry_run=False,
    )

    assert result.applied and result.verified
    assert result.old_order_match_count_after == 0
    assert result.new_order_match_count_after == 1
    assert posted["url"].endswith("/sales/transportOrder/edit")
    assert posted["json"]["id"] == 279903
    assert posted["json"]["contractNumber"] == "CONTRACT-A"
    assert posted["json"]["orderId"] == "NEW"


def test_duplicate_source_record_is_blocked(monkeypatch):
    monkeypatch.setattr(factory_edit, "_login", lambda: ("token", ""))
    monkeypatch.setattr(
        factory_edit,
        "fetch_full_order_rows",
        lambda *args, **kwargs: [_row(), _row(id=279904)],
    )

    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"orderId": "NEW"},
    )

    assert "not unique" in result.error
    assert not result.applied


def test_appointed_record_is_blocked(monkeypatch):
    monkeypatch.setattr(factory_edit, "_login", lambda: ("token", ""))
    monkeypatch.setattr(
        factory_edit,
        "fetch_full_order_rows",
        lambda *args, **kwargs: [_row(isAppointment=1)],
    )

    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"orderId": "NEW"},
    )

    assert "appointed" in result.error
    assert not result.applied


def test_unsupported_field_is_blocked_before_login(monkeypatch):
    monkeypatch.setattr(
        factory_edit,
        "_login",
        lambda: (_ for _ in ()).throw(AssertionError("login must not run")),
    )
    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"isAppointment": 0},
    )
    assert "unsupported update fields" in result.error


def test_http_failure_does_not_report_applied(monkeypatch):
    class Response:
        status_code = 500

        @staticmethod
        def raise_for_status():
            raise requests.HTTPError("500")

    monkeypatch.setattr(factory_edit, "_login", lambda: ("token", ""))
    monkeypatch.setattr(
        factory_edit, "fetch_full_order_rows", lambda *args, **kwargs: [_row()]
    )
    monkeypatch.setattr(factory_edit.requests, "post", lambda *args, **kwargs: Response())

    result = factory_edit.edit_factory_record(
        old_order_id="OLD",
        wagon_number="5248160",
        box_number="TBJU1369339",
        updates={"orderId": "NEW"},
        dry_run=False,
    )

    assert "edit request failed" in result.error
    assert not result.applied
