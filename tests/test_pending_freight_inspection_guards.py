from __future__ import annotations

import json
import sqlite3


def test_pending_freight_batch_without_rule_can_split_and_assign(tmp_path, monkeypatch):
    from sop_hub.data_agent.agent import BusinessDataAgent

    db = tmp_path / "sop.db"
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db))
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE release_batches (
      id TEXT PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE, project TEXT,
      ship_name TEXT NOT NULL, cargo_name TEXT NOT NULL,
      destination_station TEXT, notice_date TEXT NOT NULL,
      dispatch_status TEXT NOT NULL DEFAULT 'loading',
      source_json TEXT NOT NULL DEFAULT '{}', searchable_text TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE release_dispatch_match_rules (
      id TEXT PRIMARY KEY, release_batch_id TEXT NOT NULL UNIQUE,
      project TEXT, ship_name TEXT NOT NULL, destination_station TEXT,
      cargo_name TEXT NOT NULL, matching_str TEXT NOT NULL,
      matching_tokens_json TEXT NOT NULL,
      status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT, manual_note TEXT
    );
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT PRIMARY KEY, source_file_name TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT, group_name TEXT, release_batch_id TEXT,
      wagon_count INTEGER DEFAULT 0, car_numbers_json TEXT NOT NULL DEFAULT '[]',
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      message_id TEXT, project_id TEXT, document_type TEXT,
      source_image_path TEXT, extraction_json_path TEXT, parsed_json TEXT,
      ship_name TEXT, destination TEXT, cargo_name TEXT,
      candidate_status TEXT DEFAULT 'pending_match'
    );
    CREATE TABLE message_inbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT,
      raw_standard_image_path TEXT, extraction_json_path TEXT,
      received_datetime TEXT, inspection_candidate_id TEXT
    );
    """)
    conn.execute(
        "INSERT INTO release_batches "
        "(id, batch_key, project, ship_name, cargo_name, destination_station, notice_date, dispatch_status) "
        "VALUES ('lot_a','ka','zhongtang_special_steel','丰收散运','铁矿','汐子','2026-06-29','loading')"
    )
    conn.execute(
        "INSERT INTO release_batches "
        "(id, batch_key, project, ship_name, cargo_name, destination_station, notice_date, dispatch_status) "
        "VALUES ('lot_b','kb','zhongtang_special_steel','环球信任','铁矿','汐子','2026-06-29','pending_freight')"
    )
    conn.execute(
        "INSERT INTO release_dispatch_match_rules "
        "(id, release_batch_id, project, ship_name, destination_station, cargo_name, matching_str, matching_tokens_json, status) "
        "VALUES ('rule_a','lot_a','zhongtang_special_steel','丰收散运','汐子','铁矿','x',?,'active')",
        (json.dumps({"ship": ["丰收散运"], "destination": ["汐子"], "cargo": ["铁矿"]}),),
    )
    conn.commit()
    conn.close()

    payload = {
        "rows": [
            {"seq": 1, "car_no": "1000001", "cargo_info_raw": "汐子铁矿"},
            {"seq": 2, "car_no": "1000002", "cargo_info_raw": "丰收散运"},
            {"seq": 3, "car_no": "1000003", "cargo_info_raw": ""},
            {"seq": 4, "car_no": "2000001", "cargo_info_raw": "汐子铁矿"},
            {"seq": 5, "car_no": "2000002", "cargo_info_raw": "环球信任"},
            {"seq": 6, "car_no": "2000003", "cargo_info_raw": ""},
        ]
    }

    agent = BusinessDataAgent()
    groups = agent._split_payload_by_ship_rules(payload)
    assert len(groups) == 2
    huanqiu = next(g for g in groups if g.get("_split_group_ship_name") == "环球信任")
    assert huanqiu["_split_group_release_batch_id"] == "lot_b"

    res = agent.ingest_inspection_payload(payload, source_file_name="zt55.jpg")
    assert res["status"] == "multi_group"
    conn = sqlite3.connect(str(db))
    got = conn.execute(
        "SELECT ship_name, release_batch_id, candidate_status FROM inspection_ingestion_candidates "
        "ORDER BY ship_name"
    ).fetchall()
    conn.close()
    assert ("环球信任", "lot_b", "candidate") in got


def test_pending_verifier_finds_inbox_by_candidate_message_id(tmp_path):
    from sop_hub.sop.pending_match_verifier import _find_inbox_id

    db = tmp_path / "sop.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE message_inbox (
      id INTEGER PRIMARY KEY, message_id TEXT, inspection_candidate_id TEXT
    );
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT PRIMARY KEY, message_id TEXT
    );
    """)
    conn.execute("INSERT INTO inspection_ingestion_candidates VALUES ('cand1','wx_img_1')")
    conn.execute("INSERT INTO message_inbox VALUES (7,'wx_img_1',NULL)")
    conn.commit()
    conn.close()

    inbox_id, message_id = _find_inbox_id("cand1", db)
    assert (inbox_id, message_id) == (7, "wx_img_1")

    conn = sqlite3.connect(str(db))
    linked = conn.execute(
        "SELECT inspection_candidate_id FROM message_inbox WHERE id=7"
    ).fetchone()[0]
    conn.close()
    assert linked == "cand1"
