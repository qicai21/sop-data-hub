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


# ── reroute 必须写台账(否则 sync 按台账 revert) + 清 reconciled_at ──────────
def _mem_hub():
    import sqlite3
    h = sqlite3.connect(":memory:")
    h.execute("CREATE TABLE t (k TEXT PRIMARY KEY, batch_id TEXT, ship_name TEXT, "
              "reconciled_at TEXT, reconcile_source_ref TEXT)")
    h.execute("CREATE TABLE release_batches (id TEXT, ship_name TEXT)")
    h.executemany("INSERT INTO release_batches VALUES (?,?)",
                  [("b1", "和谐1"), ("b2", "诚信")])
    # 错挂 + 旧脏标记:k1 现挂 b1(和谐1)却标了已核对
    h.execute("INSERT INTO t VALUES ('k1','b1','和谐1','2026-06-28','ok:daily')")
    h.commit()
    return h


class _LedgerSpec(ReconcileSpec):
    project_id, leg, table, key_cols = "test", "x", "t", ("k",)

    def __init__(self):
        self.persisted = []

    def persist_to_ledger(self, hub, rail, routes, log=None):
        self.persisted.extend(routes)   # 记录被写台账的 (key, batch)
        return len(routes)


def test_reroute_writes_ledger_and_clears_reconciled():
    from sop_hub.reconcile import correct
    from sop_hub.reconcile.engine import MISMATCH, ReconcileResult
    hub = _mem_hub()
    correct.ensure_log_table(hub)
    spec = _LedgerSpec()
    res = ReconcileResult("test", "x", {MISMATCH: [("k1", "b1", "b2")]})
    log = correct.ReconcileLog(hub, "2026-06-28T00:00:00")
    n = correct.reroute_mismatch(spec, res, None, hub, log)
    assert n == 1
    # ① 写了台账(止 sync revert):k1 → b2
    assert spec.persisted == [("k1", "b2")]
    # ② wagon 表改归 b2/诚信 且清掉脏 reconciled_at
    row = hub.execute("SELECT batch_id, ship_name, reconciled_at FROM t WHERE k='k1'").fetchone()
    assert row == ("b2", "诚信", None)
