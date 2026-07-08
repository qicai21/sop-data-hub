from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sync_jiusan_harmony_wagons", REPO / "scripts" / "sync_jiusan_harmony_wagons.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _make_db(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE release_batches ("
        "id TEXT, project TEXT, batch_sequence TEXT, ship_name TEXT, "
        "dispatch_status TEXT, notice_date TEXT, created_at TEXT, updated_at TEXT, dispatch_status_updated_at TEXT)"
    )
    c.commit()
    return c


def test_resolve_default_ship_prefers_latest_unfinished_not_finished_harmony(tmp_path):
    c = _make_db(tmp_path)
    c.execute(
        "INSERT INTO release_batches VALUES "
        "('H1','jiusan','lot01','和谐1','confirmed_received','2026-06-09','2026-06-09','2026-07-08','2026-07-08')"
    )
    c.execute(
        "INSERT INTO release_batches VALUES "
        "('CX','jiusan','lot01','诚信','confirmed_received','2026-06-11','2026-06-11','2026-07-08','2026-07-08')"
    )
    c.execute(
        "INSERT INTO release_batches VALUES "
        "('US','jiusan','lot01','美国','enriched','2026-07-02','2026-07-02','2026-07-08','2026-07-08')"
    )
    c.commit()
    assert m.resolve_default_ship(c) == "美国"


def test_resolve_default_ship_returns_none_when_all_finished(tmp_path):
    c = _make_db(tmp_path)
    c.execute(
        "INSERT INTO release_batches VALUES "
        "('H1','jiusan','lot01','和谐1','confirmed_received','2026-06-09','2026-06-09','2026-07-08','2026-07-08')"
    )
    c.execute(
        "INSERT INTO release_batches VALUES "
        "('CX','jiusan','lot01','诚信','confirmed_received','2026-06-11','2026-06-11','2026-07-08','2026-07-08')"
    )
    c.commit()
    assert m.resolve_default_ship(c) is None
