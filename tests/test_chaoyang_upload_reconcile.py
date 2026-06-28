"""朝钢反查对账口径(2026-06-29)单元测试。

与吉林 box_no 口径对齐,但键=car_no:门户按 plan 反查返回整单全量累计,
只校验本次上传车号——present(都出现)+ unique(各自唯一);extra 仅观测。

场景:
  A. 门户有历史全量(extra 一堆)但本次车全在且唯一 → 通过
  B. 本次有车缺失 → 失败
  C. 本次车号在返回里重复 → 失败
"""
from sop_hub.external.chaoyang_ansteel.upload_wagons import reconcile_uploaded_cars


def _site(*pairs):
    """pairs: (wagonno, waybill_time) → site_wagons list[dict]."""
    return [{"wagonno": w, "waybill_time": t} for w, t in pairs]


def test_reconcile_passes_with_history_extra():
    """本次 2 车全在且唯一;门户另有历史车 → 通过(忽略 extra)。"""
    uploaded = {"C1", "C2"}
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
    # OLD* 是历史车、非今天日期 → 不计 extra;判定与 extra 无关
    assert extra == []


def test_reconcile_fails_on_missing():
    uploaded = {"C1", "C2"}
    site = _site(("C1", "20260629010101"))
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260629")
    assert missing == ["C2"]
    assert duplicate == []


def test_reconcile_fails_on_duplicate():
    """门户里 C1 出现 2 条(应唯一却重复)→ duplicate 非空。"""
    uploaded = {"C1", "C2"}
    site = _site(
        ("C1", "20260629010101"),
        ("C1", "20260629010105"),
        ("C2", "20260629010102"),
    )
    verified, missing, duplicate, extra = reconcile_uploaded_cars(
        uploaded, site, "20260629")
    assert missing == []
    assert duplicate == ["C1"]
