import os
import shutil
from pathlib import Path
import sqlite3


def _migrate_legacy_db(sop_db_path: Path) -> None:
    """R21: Auto-migrate legacy agent.db to sop_agent.db on first startup."""
    legacy_path = sop_db_path.parent / "agent.db"
    if not legacy_path.exists():
        return
    if sop_db_path.exists():
        # New DB already exists — don't overwrite
        return
    shutil.copy2(str(legacy_path), str(sop_db_path))
    import logging
    logging.getLogger("sop_hub.data_agent").info(
        "migrated legacy agent.db → %s (%s bytes)", sop_db_path, legacy_path.stat().st_size
    )


def get_db_path() -> Path:
    configured = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if configured:
        return Path(configured)
    try:
        from sop_hub.config import load_settings

        return Path(load_settings().agent_db_path)
    except Exception:
        return Path.cwd() / "data" / "sop_agent.db"


def open_db() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # R21: auto-migrate legacy agent.db on first access
    _migrate_legacy_db(db_path)

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
          cargo_product_name TEXT,
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
          dispatch_status TEXT NOT NULL DEFAULT 'in_progress' CHECK(dispatch_status IN ('in_progress', 'completed', 'suspended', 'cancelled')),
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

    migrate_wagon_shipments_schema(connection)

    migrate_shipment_release_batch_matches_schema(connection)

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
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'suspended', 'cancelled')),
          priority INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          manual_note TEXT
        )
        """
    )
    migrate_release_dispatch_match_rules_schema(connection)
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
          reconcile_plan TEXT,
          safe_to_commit INTEGER,
          requires_manual_review INTEGER,
          review_reasons TEXT,
          planned_write_count INTEGER,
          excluded_count INTEGER,
          project_archive_paths TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_ingestion_audit_group ON image_ingestion_audit(group_name, created_at)"
    )
    migrate_image_ingestion_audit_schema(connection)
    connection.commit()

    return connection


def migrate_release_dispatch_match_rules_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='release_dispatch_match_rules'"
    ).fetchone()
    sql = row["sql"] if row else ""
    if "CHECK(status IN ('active', 'completed', 'suspended'))" not in (sql or ""):
        return
    connection.execute("ALTER TABLE release_dispatch_match_rules RENAME TO release_dispatch_match_rules_old")
    connection.execute(
        """
        CREATE TABLE release_dispatch_match_rules (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT NOT NULL UNIQUE,
          project TEXT,
          ship_name TEXT NOT NULL,
          destination_station TEXT,
          cargo_name TEXT NOT NULL,
          matching_str TEXT NOT NULL,
          matching_tokens_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'completed', 'suspended', 'cancelled')),
          priority INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          manual_note TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO release_dispatch_match_rules (
          id, release_batch_id, project, ship_name, destination_station, cargo_name,
          matching_str, matching_tokens_json, status, priority, created_at, updated_at,
          completed_at, manual_note
        )
        SELECT id, release_batch_id, project, ship_name, destination_station, cargo_name,
               matching_str, matching_tokens_json, status, priority, created_at, updated_at,
               completed_at, manual_note
        FROM release_dispatch_match_rules_old
        """
    )
    connection.execute("DROP TABLE release_dispatch_match_rules_old")
    connection.commit()


def migrate_image_ingestion_audit_schema(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(image_ingestion_audit)").fetchall()}
    new_fields = {
        "reconcile_plan": "TEXT",
        "safe_to_commit": "INTEGER",
        "requires_manual_review": "INTEGER",
        "review_reasons": "TEXT",
        "planned_write_count": "INTEGER",
        "excluded_count": "INTEGER",
        "project_archive_paths": "TEXT",
    }
    for field, type_def in new_fields.items():
        if field not in columns:
            connection.execute(f"ALTER TABLE image_ingestion_audit ADD COLUMN {field} {type_def}")


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
        "cargo_product_name": "TEXT",
        "order_identifier": "TEXT",
        "cargo_name_detail": "TEXT",
        "quantity_tons": "REAL",
        "source_message_id": "TEXT",
        "source_group_id": "TEXT",
        # 中唐特钢业务:海铁联运两段船(A船在青岛进口 → B船内贸转水到锦州港),
        # 原 ship_name 用作到港船名,新增 import_ship_name 存进口段大船名。
        # 发运 excel footer 要两个都展示。补充货运信息文字消息与出港通知单船名
        # 不一致时,人工指认 + 双存。
        "import_ship_name": "TEXT",
    }
    
    for field, type_def in new_fields.items():
        if field not in columns:
            connection.execute(f"ALTER TABLE release_batches ADD COLUMN {field} {type_def}")

    connection.commit()


def migrate_wagon_shipments_schema(connection: sqlite3.Connection) -> None:
    """R45: Ensure wagon_shipments has extended columns for create_wagon_shipments."""
    try:
        connection.execute("SELECT 1 FROM wagon_shipments LIMIT 0")
    except sqlite3.OperationalError:
        # Table doesn't exist yet — create minimal schema
        connection.execute("""
            CREATE TABLE IF NOT EXISTS wagon_shipments (
                id TEXT PRIMARY KEY,
                departure_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                car_no TEXT,
                car_model TEXT NOT NULL DEFAULT '',
                cargo_name TEXT NOT NULL DEFAULT '',
                shipper_name TEXT NOT NULL DEFAULT '',
                consignee_name TEXT NOT NULL DEFAULT '',
                origin_name TEXT NOT NULL DEFAULT '',
                destination_name TEXT NOT NULL DEFAULT '',
                ticketed_at TEXT NOT NULL DEFAULT '',
                departed_at TEXT NOT NULL DEFAULT '',
                arrived_at TEXT NOT NULL DEFAULT '',
                status_name TEXT NOT NULL DEFAULT '',
                freight_fee REAL NOT NULL DEFAULT 0,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(wagon_shipments)").fetchall()
    }
    new_fields: dict[str, str] = {
        "delivered_at": "TEXT",
        "confirmed_received_at": "TEXT",
        "container_no": "TEXT",
        "waybill_no": "TEXT",
        "project_id": "TEXT",
        "ship_name": "TEXT",
        "dispatch_status": "TEXT NOT NULL DEFAULT 'in_progress'",
        "source_message_id": "TEXT",
        "source_group_id": "TEXT",
    }
    for field, type_def in new_fields.items():
        if field not in columns:
            connection.execute(f"ALTER TABLE wagon_shipments ADD COLUMN {field} {type_def}")
    connection.commit()


def migrate_shipment_release_batch_matches_schema(connection: sqlite3.Connection) -> None:
    """R45.1: Create shipment_release_batch_matches in sop_agent.db.

    This is a local table linking wagon_shipments to their 95306 source records.
    NOT the same table as the 95306 DB's shipment_release_batch_matches.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
            id TEXT PRIMARY KEY,
            release_batch_id TEXT NOT NULL,
            wagon_shipment_id TEXT NOT NULL,
            ydid TEXT NOT NULL,
            waybill_no TEXT DEFAULT '',
            wagon_no TEXT NOT NULL,
            container_no TEXT DEFAULT '',
            match_source TEXT DEFAULT 'departure_text_match',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_sop_matches_batch "
        "ON shipment_release_batch_matches(release_batch_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_sop_matches_wagon "
        "ON shipment_release_batch_matches(wagon_shipment_id)"
    )
    connection.commit()
