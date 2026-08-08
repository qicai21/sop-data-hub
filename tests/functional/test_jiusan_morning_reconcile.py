"""#issue-20260713: 九三早间合并对账每日成功一次。"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_jiusan_morning_reconcile", REPO / "scripts" / "run_jiusan_morning_reconcile.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_successful_run_is_idempotent_for_the_same_business_day(tmp_path, monkeypatch):
    db = tmp_path / "sop_agent.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE container_pool_snapshot (project TEXT, ship_name TEXT, snapshot_date TEXT)"
    )
    conn.execute(
        "INSERT INTO container_pool_snapshot VALUES ('jiusan','九三大豆','2026-07-13')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(m, "DB", db)
    monkeypatch.setattr(m, "now_iso_beijing", lambda: "2026-07-13T08:00:00+08:00")

    morning_calls = []
    reconcile_calls = []
    monkeypatch.setitem(sys.modules, "jiusan_morning_report_ingest", types.SimpleNamespace(
        main=lambda *, apply: morning_calls.append(apply),
    ))
    monkeypatch.setitem(sys.modules, "run_reconcile", types.SimpleNamespace(
        run=lambda *args, **kwargs: reconcile_calls.append((args, kwargs)),
    ))

    assert m.run()["status"] == "succeeded"
    assert m.run()["status"] == "skipped"
    assert morning_calls == [True]
    assert len(reconcile_calls) == 1
