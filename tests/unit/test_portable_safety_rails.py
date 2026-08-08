"""Phase 0: portable safety rails and schema helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_production_sop_db_write_is_blocked(project_root: Path):
    prod = project_root / "data" / "sop_agent.db"
    with pytest.raises(RuntimeError, match="must not open production SOP DB"):
        sqlite3.connect(str(prod))


def test_tmp_sqlite_still_works(tmp_path: Path):
    db = tmp_path / "ok.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.close()


def test_sop_db_fixture_uses_production_schema(sop_db: Path):
    conn = sqlite3.connect(str(sop_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert "release_batches" in tables
    assert "wagon_shipments" in tables


def test_rail_db_has_min_contract_columns(rail_db: Path):
    from tests.support.rail_min import MIN_RAIL_SHIPMENTS_COLUMNS

    conn = sqlite3.connect(str(rail_db))
    try:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(shipments)").fetchall()
        }
    finally:
        conn.close()
    assert MIN_RAIL_SHIPMENTS_COLUMNS <= cols


def test_all_http_egress_is_blocked():
    import requests

    with pytest.raises(RuntimeError, match="block all live HTTP egress"):
        requests.get("https://56.ansteel.com.cn/api/encryptLogin", timeout=1)
    with pytest.raises(RuntimeError, match="block all live HTTP egress"):
        requests.get("http://127.0.0.1:8021/v1/chat/completions", timeout=1)
    with pytest.raises(RuntimeError, match="block all live HTTP egress"):
        requests.Session().post("https://example.com/api", timeout=1)


def test_wechat_send_is_stubbed():
    from sop_hub.sop import send_excel

    result = send_excel.send_to_wechat(target="x", message="y", file_path=None)
    assert result.success is True
