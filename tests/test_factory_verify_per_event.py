"""Per-event 反查判定口径(2026-06-29)单元测试。

覆盖用户口径:门户按计划号反查必返整单全量累计(extra/total 对不上是正常),
判定只看本次上传的键是否——
  1) 全部出现(present / missing==0)
  2) 各自唯一(unique / duplicate==0)

三个核心场景:
  A. 订单有历史累计(extra 一堆、total 对不上)但本次箱全在且唯一 → 通过
  B. 本次有箱缺失 → 失败
  C. 本次箱在门户返回里重复 → 失败
"""
from collections import Counter

from sop_hub.sop import factory_verify
from sop_hub.sop.factory_verify import (
    evaluate_presence_and_uniqueness,
    verify_factory_upload,
)


# ── 纯判定函数(逻辑核心)──────────────────────────────────────────────

def test_eval_present_and_unique_passes_despite_history():
    """本次 3 箱全在、各 1 条;门户另有 600 条历史箱 → 通过(忽略 extra/total)。"""
    expected = {"B1", "B2", "B3"}
    counts = Counter({"B1": 1, "B2": 1, "B3": 1})
    # 模拟门户整单累计里还有一大堆别的箱
    for i in range(600):
        counts[f"H{i}"] = 1
    missing, duplicate = evaluate_presence_and_uniqueness(expected, counts)
    assert missing == []
    assert duplicate == []


def test_eval_missing_box_fails():
    """本次 3 箱,门户少了 B3 → missing 非空。"""
    expected = {"B1", "B2", "B3"}
    counts = Counter({"B1": 1, "B2": 1})
    missing, duplicate = evaluate_presence_and_uniqueness(expected, counts)
    assert missing == ["B3"]
    assert duplicate == []


def test_eval_duplicate_box_fails():
    """本次 3 箱,门户里 B2 出现 2 条(应唯一却重复)→ duplicate 非空。"""
    expected = {"B1", "B2", "B3"}
    counts = Counter({"B1": 1, "B2": 2, "B3": 1})
    missing, duplicate = evaluate_presence_and_uniqueness(expected, counts)
    assert missing == []
    assert duplicate == ["B2"]


# ── 端到端(monkeypatch 门户)验证 all_boxes_found 判定 ──────────────────

class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def _patch_portal(monkeypatch, returned_box_counts: Counter, total: int):
    """让 verify_factory_upload 不连真门户:登录直接成功,list 按页返回构造的整单。"""
    monkeypatch.setattr(factory_verify, "_login", lambda: ("tok", ""))

    rows = []
    for bn, cnt in returned_box_counts.items():
        rows.extend({"boxNumber": bn} for _ in range(cnt))

    def _fake_get(url, params=None, headers=None, timeout=None):
        # 真实门户分页:按 pageNum/pageSize 切片(否则每页全量会重复计数)
        params = params or {}
        size = int(params.get("pageSize", 100))
        page = int(params.get("pageNum", 1))
        return _FakeResp({"total": total, "rows": rows[(page - 1) * size: page * size]})

    monkeypatch.setattr(factory_verify.requests, "get", _fake_get)


def test_verify_passes_with_history_extra(monkeypatch):
    """A:本次 2 箱全在且唯一,门户整单累计 875 条 → all_boxes_found=True。"""
    portal = Counter({"X1": 1, "X2": 1})
    for i in range(873):
        portal[f"OLD{i}"] = 1
    _patch_portal(monkeypatch, portal, total=875)

    s = verify_factory_upload(
        order_id="ORD1",
        expected_box_numbers={"X1", "X2"},
        expected_count=2,
    )
    assert s.all_boxes_found is True       # 通过
    assert s.missing_boxes == []
    assert s.duplicate_boxes == []
    # 观测字段仍计算:total 对不上、extra 一堆,但不影响判定
    assert s.total_match is False
    assert len(s.extra_boxes) == 873


def test_verify_fails_on_missing(monkeypatch):
    """B:门户缺 X2 → all_boxes_found=False。"""
    portal = Counter({"X1": 1})
    _patch_portal(monkeypatch, portal, total=1)
    s = verify_factory_upload(
        order_id="ORD1",
        expected_box_numbers={"X1", "X2"},
        expected_count=2,
    )
    assert s.all_boxes_found is False
    assert s.missing_boxes == ["X2"]
    assert s.duplicate_boxes == []


def test_verify_fails_on_duplicate(monkeypatch):
    """C:门户里 X1 出现 2 条 → all_boxes_found=False(唯一性冲突)。"""
    portal = Counter({"X1": 2, "X2": 1})
    _patch_portal(monkeypatch, portal, total=3)
    s = verify_factory_upload(
        order_id="ORD1",
        expected_box_numbers={"X1", "X2"},
        expected_count=2,
    )
    assert s.all_boxes_found is False
    assert s.missing_boxes == []
    assert s.duplicate_boxes == ["X1"]
