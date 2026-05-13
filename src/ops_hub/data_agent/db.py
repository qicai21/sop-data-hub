import os
from pathlib import Path
import sqlite3


def get_db_path() -> Path:
    configured = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if configured:
        return Path(configured)
    try:
        from ops_hub.config import load_settings

        return Path(load_settings().agent_db_path)
    except Exception:
        return Path.cwd() / "data" / "agent.db"


def open_db() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    
    # 1. Create Contracts Table
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS contracts (
          id TEXT PRIMARY KEY,
          party_a TEXT NOT NULL,
          party_b TEXT NOT NULL,
          origin_station TEXT,
          destination_station TEXT,
          transport_mode TEXT,
          transport_type TEXT,
          price REAL,
          cargo_name TEXT,
          doc_path TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # 2. Create/Update Release Batches Table
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS release_batches (
          id TEXT PRIMARY KEY,
          batch_key TEXT NOT NULL UNIQUE,
          project TEXT,
          contract_id TEXT,
          contract_no TEXT,
          ship_name TEXT NOT NULL,
          cargo_name TEXT NOT NULL,
          consignor TEXT,
          consignee TEXT,
          commissioner_identifier TEXT,
          commissioner_note TEXT,
          trade_type TEXT,
          transport_mode TEXT,
          destination_station TEXT,
          yard_location TEXT,
          customs_release_qty REAL,
          notice_date TEXT NOT NULL,
          batch_date TEXT,
          batch_sequence TEXT,
          batch_quantity REAL,
          total_planned_quantity REAL,
          remaining_quantity REAL,
          batch_count INTEGER NOT NULL DEFAULT 0,
          origin_station TEXT,
          agent_name TEXT,
          customer_name TEXT,
          id_label TEXT,
          actual_wagon_count INTEGER DEFAULT 0,
          dispatch_status TEXT NOT NULL DEFAULT 'in_progress' CHECK(dispatch_status IN ('in_progress', 'completed', 'suspended')),
          dispatch_status_note TEXT,
          dispatch_status_updated_at TEXT,
          source_file_name TEXT,
          source_json TEXT NOT NULL,
          searchable_text TEXT NOT NULL,
          plan_id TEXT,
          order_id TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    migrate_release_batches_schema(connection)

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_release_batches_notice_date ON release_batches(notice_date)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_release_batches_ship_name ON release_batches(ship_name)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_contracts_party_b ON contracts(party_b)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS release_dispatch_match_rules (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT NOT NULL UNIQUE,
          project TEXT,
          ship_name TEXT NOT NULL,
          destination_station TEXT,
          cargo_name TEXT NOT NULL,
          matching_str TEXT NOT NULL,
          matching_tokens_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'suspended')),
          priority INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          manual_note TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_release_dispatch_match_rules_status ON release_dispatch_match_rules(status, priority, updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_release_dispatch_match_rules_ship ON release_dispatch_match_rules(ship_name, destination_station, cargo_name)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS inspection_ingestion_candidates (
          id TEXT PRIMARY KEY,
          source_file_name TEXT NOT NULL,
          status TEXT NOT NULL,
          reason TEXT,
          group_name TEXT,
          release_batch_id TEXT,
          wagon_count INTEGER DEFAULT 0,
          car_numbers_json TEXT NOT NULL DEFAULT '[]',
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS image_ingestion_audit (
          id TEXT PRIMARY KEY,
          group_name TEXT,
          group_id TEXT,
          message_time TEXT,
          sender TEXT,
          message_id TEXT,
          local_id TEXT,
          message_type TEXT NOT NULL DEFAULT 'image',
          raw_image_path TEXT,
          classified_category TEXT,
          classification_confidence REAL,
          classified_image_path TEXT,
          extraction_json_path TEXT,
          project_id TEXT,
          target_node TEXT,
          adopted_fields TEXT,
          ignored_fields TEXT,
          db_action TEXT,
          db_tables TEXT,
          db_record_ids TEXT,
          status TEXT,
          reason TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_ingestion_audit_group ON image_ingestion_audit(group_name, created_at)"
    )
    connection.commit()

    return connection


def migrate_release_batches_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(release_batches)").fetchall()
    }

    # Handle older migrations
    if "latest_batch_date" in columns and "batch_date" not in columns:
        connection.execute(
            "ALTER TABLE release_batches RENAME COLUMN latest_batch_date TO batch_date"
        )
    if "latest_batch_sequence" in columns and "batch_sequence" not in columns:
        connection.execute(
            "ALTER TABLE release_batches RENAME COLUMN latest_batch_sequence TO batch_sequence"
        )
    if "latest_batch_quantity" in columns and "batch_quantity" not in columns:
        connection.execute(
            "ALTER TABLE release_batches RENAME COLUMN latest_batch_quantity TO batch_quantity"
        )

    # Add new business fields if they don't exist
    new_fields = {
        "origin_station": "TEXT",
        "project": "TEXT",
        "commissioner_identifier": "TEXT",
        "commissioner_note": "TEXT",
        "agent_name": "TEXT",
        "customer_name": "TEXT",
        "id_label": "TEXT",
        "actual_wagon_count": "INTEGER DEFAULT 0",
        "dispatch_status": "TEXT NOT NULL DEFAULT 'in_progress'",
        "dispatch_status_note": "TEXT",
        "dispatch_status_updated_at": "TEXT",
        "is_weighed": "INTEGER DEFAULT 0",
        "loading_weight": "REAL",
        "return_weight": "REAL",
        "tail_cargo_weight": "REAL",
        "tail_cargo_status": "TEXT",
        "tail_cargo_remark": "TEXT",
        "plan_id": "TEXT",
        "order_id": "TEXT",
    }
    
    for field, type_def in new_fields.items():
        if field not in columns:
            connection.execute(f"ALTER TABLE release_batches ADD COLUMN {field} {type_def}")

    connection.commit()
