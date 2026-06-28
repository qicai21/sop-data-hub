"""通用对账引擎守门:五分类真值表 + 已核对剔除。

用假 Spec(三方各返固定 dict),不碰 95306/WeChat,纯验引擎判定逻辑。
改对账引擎前必跑。
"""
from __future__ import annotations

from sop_hub.reconcile.engine import (
    MISMATCH, MISSING, NEW_UNATTR, OK, PHANTOM, ReconcileSpec, reconcile,
)


class FakeSpec(ReconcileSpec):
    project_id = "test"
    leg = "x"

    def __init__(self, U, D, S, done=None):
        self._U, self._D, self._S, self._done = U, D, S, set(done or ())

    def universe(self, rail):
        return dict(self._U)

    def db_rows(self, hub):
        return dict(self._D)

    def source_batch(self, rail, hub):
        return dict(self._S)

    def reconciled_keys(self, hub):
        return set(self._done)


def test_truth_table_five_categories():
    # k1 一致;k2 错挂;k3 漏入库;k4 新货(源未到);k5 幻影(95306无)
    U = {"k1": {}, "k2": {}, "k3": {}, "k4": {}}
    D = {"k1": "b1", "k2": "bX", "k5": "b9"}
    S = {"k1": "b1", "k2": "b1", "k3": "b1"}
    r = reconcile(FakeSpec(U, D, S), None, None)
    assert r.by_cat[OK] == ["k1"]
    assert r.by_cat[MISMATCH] == [("k2", "bX", "b1")]
    assert r.by_cat[MISSING] == [("k3", "b1")]
    assert r.by_cat[NEW_UNATTR] == [("k4", None)]          # d 缺省 None
    assert r.by_cat[PHANTOM] == ["k5"]


def test_new_unattr_carries_current_batch():
    # 新货已被默认堆某船:NEW_UNATTR 要带上当前 batch(供完成闸判断)
    r = reconcile(FakeSpec({"k": {}}, {"k": "finished_ship"}, {}), None, None)
    assert r.by_cat[NEW_UNATTR] == [("k", "finished_ship")]


def test_reconciled_keys_excluded_from_all_three():
    # 已核对的 k1 从三方剔除,不再参与;只剩 k2 判 OK
    U = {"k1": {}, "k2": {}}
    D = {"k1": "b1", "k2": "b1"}
    S = {"k1": "b1", "k2": "b1"}
    r = reconcile(FakeSpec(U, D, S, done={"k1"}), None, None)
    assert r.by_cat[OK] == ["k2"]
    assert r.universe_n == 1 and r.db_n == 1   # 剔除后只剩 1


def test_empty_source_all_new():
    # 源全没到 → 全 new_unattributed,不误判 missing/phantom
    r = reconcile(FakeSpec({"a": {}, "b": {}}, {"a": "b1", "b": "b1"}, {}), None, None)
    assert r.n(NEW_UNATTR) == 2
    assert r.n(MISSING) == 0 and r.n(PHANTOM) == 0


def test_tuple_keys_box_level():
    # 集装箱 (ydid, box) 元组 key 也能正确分类
    U = {("y1", "b1"): {}, ("y1", "b2"): {}}
    D = {("y1", "b1"): "和谐1", ("y1", "b2"): "和谐1"}
    S = {("y1", "b1"): "和谐1", ("y1", "b2"): "诚信"}   # 一票两船:b2 应诚信
    r = reconcile(FakeSpec(U, D, S), None, None)
    assert r.by_cat[OK] == [("y1", "b1")]
    assert r.by_cat[MISMATCH] == [(("y1", "b2"), "和谐1", "诚信")]
