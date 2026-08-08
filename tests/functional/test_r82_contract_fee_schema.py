from __future__ import annotations

import sqlite3

from sop_hub.data_agent import db as db_module


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_open_db_creates_r82_tables_and_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "sop_agent.db"
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db_path))

    conn = db_module.open_db()
    try:
        assert {"freight_fee", "detail_json", "loading_line"} <= _cols(conn, "wagon_container_shipments")
        assert {"pricing_basis", "pricing_unit", "default_rate"} <= _cols(conn, "contract_fee_terms")
        assert {"line_name", "rate", "pricing_unit"} <= _cols(conn, "contract_line_rates")
        assert {"weight_type", "confirmed_weight", "route_code"} <= _cols(conn, "shipment_weight_confirmation")
    finally:
        conn.close()


def test_r82_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "sop_agent.db"
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db_path))

    conn = db_module.open_db()
    conn.close()
    conn = db_module.open_db()
    try:
        assert {"freight_fee", "detail_json", "loading_line"} <= _cols(conn, "wagon_container_shipments")
        assert {"contract_fee_terms", "contract_line_rates", "shipment_weight_confirmation"} <= {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
