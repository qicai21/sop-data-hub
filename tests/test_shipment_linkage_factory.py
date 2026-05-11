from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ops_hub.matching.release_match_spec import build_match_spec, row_matches_spec
from ops_hub.matching.shipment_linkage import link_release_batch_to_inspection


def test_match_spec_expands_chaoyang_steel_alias_patterns() -> None:
    spec = build_match_spec(
        {
            "id": "batch-chaoyang",
            "ship_name": "合远9",
            "cargo_name": "铁矿粉",
            "destination_station": "朝阳西",
            "project": "朝钢铁矿发运项目",
        }
    )

    assert "朝阳西/铁矿粉/*?/合远9" in spec.patterns
    assert "朝阳铁/*?/合远9" in spec.patterns
    assert row_matches_spec({"cargo_info_effective": "朝阳西铁矿粉 合远9"}, spec)
    assert row_matches_spec({"cargo_info_effective": "朝阳铁 合远9"}, spec)


def test_match_spec_expands_zhongtang_xizi_iron_patterns() -> None:
    spec = build_match_spec(
        {
            "id": "batch-zt",
            "ship_name": "贝拉",
            "cargo_name": "铁矿",
            "destination_station": "汐子",
            "project": "中唐特钢铁矿发运项目",
        }
    )

    assert "汐子/铁" in spec.patterns
    assert "汐子铁矿(粉)?" in spec.patterns
    assert row_matches_spec({"cargo_info_effective": "汐子铁矿粉"}, spec)
    assert row_matches_spec({"cargo_info_raw": "汐子铁"}, spec)


def test_link_release_batch_to_inspection_writes_matches_from_release_spec(tmp_path: Path) -> None:
    rail_db = tmp_path / "rail.sqlite3"
    create_rail_schema(rail_db)
    insert_shipments(
        rail_db,
        [
            ("yd1", "100001", "C70", "铁矿粉", "汐子", "20260503", "2026-05-03 01:00:00"),
            ("yd2", "100002", "C70E", "铁矿粉", "汐子", "20260503", "2026-05-03 01:00:10"),
        ],
    )
    inspection_json = tmp_path / "inspection.json"
    inspection_json.write_text(
        json.dumps(
            {
                "rows": [
                    {"seq": 1, "car_no": "100001", "car_type": "70", "cargo_info_effective": "汐子铁矿粉", "defect": False},
                    {"seq": 2, "car_no": "100002", "car_type": "70E", "cargo_info_effective": "汐子铁矿粉", "defect": False},
                ],
                "footer": {"zhuangche_jieshu": 2, "paiche_jieshu": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = link_release_batch_to_inspection(
        {
            "id": "batch-1",
            "batch_sequence": "lot03",
            "batch_date": "2026-05-03",
            "ship_name": "贝拉",
            "cargo_name": "铁矿",
            "destination_station": "汐子",
        },
        inspection_json,
        rail_db,
        write=True,
    )

    assert result.triggered is True
    assert result.linked_rows == 2
    conn = sqlite3.connect(rail_db)
    rows = conn.execute(
        "select release_batch_id, shipment_ydid, shipment_car_no, inspection_row, ticketed_at from shipment_release_batch_matches order by inspection_row"
    ).fetchall()
    assert rows == [
        ("batch-1", "yd1", "100001", 1, "2026-05-03 01:00:00"),
        ("batch-1", "yd2", "100002", 2, "2026-05-03 01:00:10"),
    ]


def create_rail_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE shipments (
          ydid TEXT PRIMARY KEY,
          car_no TEXT,
          car_model TEXT,
          cargo_name TEXT,
          marked_weight TEXT,
          loaded_at TEXT,
          ticketed_at TEXT,
          origin_name TEXT,
          destination_name TEXT,
          transport_mode_name TEXT,
          status_code TEXT,
          status_name TEXT,
          latest_stage_name TEXT,
          latest_event_time TEXT
        );
        CREATE TABLE shipment_release_batch_matches (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT NOT NULL,
          release_batch_sequence TEXT NOT NULL,
          release_batch_date TEXT,
          inspection_file TEXT NOT NULL,
          inspection_row INTEGER NOT NULL,
          inspection_car_no TEXT,
          inspection_car_type TEXT,
          shipment_ydid TEXT NOT NULL,
          shipment_car_no TEXT NOT NULL,
          shipment_car_model TEXT,
          planned_weight REAL,
          marked_weight TEXT,
          loaded_at TEXT,
          origin_name TEXT,
          destination_name TEXT,
          cargo_name TEXT,
          transport_mode_name TEXT,
          status_code TEXT,
          status_name TEXT,
          latest_stage_name TEXT,
          latest_event_time TEXT,
          match_rule TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(release_batch_id, shipment_ydid)
        );
        """
    )
    conn.commit()
    conn.close()


def insert_shipments(path: Path, rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    for ydid, car_no, car_model, cargo_name, destination, loaded_at, ticketed_at in rows:
        conn.execute(
            """
            INSERT INTO shipments (
              ydid, car_no, car_model, cargo_name, marked_weight, loaded_at, ticketed_at,
              origin_name, destination_name, transport_mode_name, status_code, status_name,
              latest_stage_name, latest_event_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '高桥镇', ?, '整车运输', '80', '货物已交付', '交付', '2026-05-03 12:00:00')
            """,
            (ydid, car_no, car_model, cargo_name, "70.00", loaded_at, ticketed_at, destination),
        )
    conn.commit()
    conn.close()
