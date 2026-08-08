"""朝钢反查对账口径单元测试。

门户按 plan 反查返回整单累计,且现场已确认 `WAYBILL_TIME` 更像门户写入时间,
不是铁路 `ticketed_at`。因此朝钢当前稳定口径是:

- 只看"今天返回"里的 car_no
- present = 本次车号都在今天返回里
- unique = 本次车号在今天返回里各自仅一条
"""

from sop_hub.external.chaoyang_ansteel.upload_wagons import (
    WagonForUpload,
    reconcile_uploaded_cars,
)


def _site(*pairs):
    return [{"wagonno": w, "waybill_time": t} for w, t in pairs]


def _uploaded(*pairs):
    return [WagonForUpload(w, t) for w, t in pairs]


def test_reconcile_passes_with_history_extra():
    uploaded = _uploaded(
        ("C1", "20260629010101"),
        ("C2", "20260629010102"),
    )
    site = _site(
        ("C1", "20260629010101"),
        ("C2", "20260629010102"),
        ("OLD1", "20260601090000"),
        ("OLD2", "20260601090001"),
    )
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260629")
    assert missing == []
    assert duplicate == []
    assert verified == {"C1", "C2"}
    assert extra == []


def test_reconcile_fails_on_missing():
    uploaded = _uploaded(
        ("C1", "20260629010101"),
        ("C2", "20260629010102"),
    )
    site = _site(("C1", "20260629010101"))
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260629")
    assert verified == {"C1"}
    assert missing == ["C2"]
    assert duplicate == []


def test_reconcile_fails_on_duplicate():
    uploaded = _uploaded(
        ("C1", "20260629010101"),
        ("C2", "20260629010102"),
    )
    site = _site(
        ("C1", "20260629010101"),
        ("C1", "20260629010101"),
        ("C2", "20260629010102"),
    )
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260629")
    assert missing == []
    assert duplicate == ["C1"]
    assert verified == {"C1", "C2"}


def test_reconcile_ignores_old_day_same_car_and_requires_today_presence():
    uploaded = _uploaded(
        ("C1", "20260708110529"),
        ("D2", "20260708110529"),
    )
    site = _site(
        ("C1", "20260705185755"),
        ("D2", "20260708110529"),
    )
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260708")
    assert verified == {"D2"}
    assert missing == ["C1"]
    assert duplicate == []
    assert extra == []


def test_reconcile_reports_today_extra_cars_only():
    uploaded = _uploaded(
        ("C1", "20260708133742"),
    )
    site = _site(
        ("C1", "20260708133742"),
        ("X9", "20260708110529"),
        ("OLD1", "20260705185755"),
    )
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260708")
    assert verified == {"C1"}
    assert missing == []
    assert duplicate == []
    assert extra == ["X9"]
