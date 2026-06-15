"""factory_remove:批量删除工厂门户记录(dry_run 默认 + 录入日期过滤 + 复查)。

全程 mock HTTP,不碰真实门户。
"""
from __future__ import annotations

import sop_hub.sop.factory_remove as fr


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _rows(n_today, n_old):
    rows = []
    rid = 1000
    for _ in range(n_today):
        rows.append({"id": rid, "wagonNumber": f"C{rid}", "boxNumber": f"B{rid}",
                     "recordDate": "2026-06-15 15:08:00"})
        rid += 1
    for _ in range(n_old):
        rows.append({"id": rid, "wagonNumber": f"C{rid}", "boxNumber": f"B{rid}",
                     "recordDate": "2026-06-13 12:52:00"})
        rid += 1
    return rows


def _patch(monkeypatch, rows_seq):
    """rows_seq: list of row-lists,每次 fetch_order_rows 调用返回下一个。"""
    calls = {"get": 0, "post": []}
    state = {"rows": list(rows_seq)}

    def fake_get(url, params=None, headers=None, timeout=None):
        idx = min(calls["get"], len(state["rows"]) - 1)
        calls["get"] += 1
        return _Resp(200, {"rows": state["rows"][idx], "total": len(state["rows"][idx])})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"].append(json)
        return _Resp(200, {})

    monkeypatch.setattr(fr, "_login", lambda: ("tok", ""))
    monkeypatch.setattr(fr.requests, "get", fake_get)
    monkeypatch.setattr(fr.requests, "post", fake_post)
    monkeypatch.setattr(fr.time, "sleep", lambda *_: None)
    return calls


def test_dry_run_previews_no_delete(monkeypatch):
    calls = _patch(monkeypatch, [_rows(3, 2)])
    s = fr.remove_order(order_id="O1", dry_run=True)
    assert s.login_ok and s.matched == 5 and s.removed == 0
    assert calls["post"] == []          # dry_run 不发删除


def test_record_date_filter(monkeypatch):
    # 只删今天 → 命中 3(不含 06-13 那 2)
    calls = _patch(monkeypatch, [_rows(3, 2), _rows(0, 2)])
    s = fr.remove_order(order_id="O1", record_date_prefix="2026-06-15", dry_run=False)
    assert s.matched == 3
    assert s.removed == 3
    assert s.remaining_matched == 0     # 复查今天已清
    # post 只发了今天那 3 个 id
    flat = [i for chunk in calls["post"] for i in chunk]
    assert len(flat) == 3


def test_apply_deletes_and_reverifies(monkeypatch):
    # 第一次查 5 条,删后复查 0 条
    _patch(monkeypatch, [_rows(3, 2), []])
    s = fr.remove_order(order_id="O1", dry_run=False)
    assert s.matched == 5 and s.removed == 5 and s.remaining_matched == 0


def test_no_order_id_errors(monkeypatch):
    monkeypatch.setattr(fr, "_login", lambda: ("tok", ""))
    s = fr.remove_order(order_id=None)
    assert s.error and not s.login_ok    # 没 order_id 直接报错,不登录
