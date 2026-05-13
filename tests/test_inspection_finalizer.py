from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ops_hub.matching.inspection_finalizer import finalize_inspection_candidates
from ops_hub.matching.shipment_linkage import _ensure_match_table


def test_candidate_status_does_not_write_formal_table_until_commit(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)

    before = _match_count(rail_db, "batch-1")
    result = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="dry_run",
        operator_note="pytest dry-run",
    )

    assert before == 0
    assert result.satisfied is True
    assert result.planned_write_count == 2
    assert _match_count(rail_db, "batch-1") == 0


def test_commit_writes_matches_and_repeated_commit_is_idempotent(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)

    first = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest commit",
    )
    second = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest commit rerun",
    )

    assert first.committed_count == 2
    assert second.committed_count == 2
    assert _match_count(rail_db, "batch-1") == 2
    assert _business_actual_wagon_count(biz_db, "batch-1") == 2


def test_mixed_ship_candidate_only_writes_target_release_segment(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True, include_other_ship_segment=True)

    result = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest mixed ship",
    )

    assert result.planned_write_count == 2
    assert _match_count(rail_db, "batch-1") == 2
    cars = _matched_cars(rail_db, "batch-1")
    assert cars == ["100001", "100002"]
    assert any(item["wagon_no"] == "200001" and item["reason"] == "outside-target-release-segment" for item in result.excluded)


def test_unauthorized_project_or_route_cannot_commit(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=False)

    result = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest unauthorized",
    )

    assert result.satisfied is False
    assert result.reason == "candidate-sop-not-authorized"
    assert _match_count(rail_db, "batch-1") == 0


def test_anzihe_lot04_scope_is_fixed_when_live_databases_exist() -> None:
    biz_db = Path("/Users/qicai21/projects/repos/wx-ops-agent/data/agent.db")
    rail_db = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
    if not biz_db.exists() or not rail_db.exists():
        pytest.skip("machine-local Anzihe lot04 databases are not present")

    result = finalize_inspection_candidates(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="88ceb9b2086fed6e81cd4eadb8b2b0a0002c8c01",
        run_mode="dry_run",
        operator_note="pytest anzihe lot04 dry-run",
    )

    assert result.satisfied is True
    assert result.planned_write_count == 164
    assert result.matched_95306_count == 164


def _build_fixture(tmp_path: Path, *, authorized: bool, include_other_ship_segment: bool = False) -> tuple[Path, Path]:
    biz_db = tmp_path / "business.sqlite3"
    rail_db = tmp_path / "rail.sqlite3"
    _create_business_schema(biz_db)
    _create_rail_schema(rail_db)
    conn = sqlite3.connect(biz_db)
    payload_rows = [
        {"seq": 1, "car_no": "100001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉", "defect": False},
        {"seq": 2, "car_no": "100002", "car_type": "70", "cargo_info_raw": "鞍子河", "cargo_info_effective": "汐子铁矿粉/鞍子河", "defect": False},
        {"seq": 3, "car_no": "100003", "car_type": "70", "cargo_info_raw": "2节", "cargo_info_effective": "汐子铁矿粉/2节", "defect": False},
    ]
    if include_other_ship_segment:
        payload_rows.extend(
            [
                {"seq": 4, "car_no": "200001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉", "defect": False},
                {"seq": 5, "car_no": "200002", "car_type": "70", "cargo_info_raw": "贝拉", "cargo_info_effective": "汐子铁矿粉/贝拉", "defect": False},
            ]
        )
    payload = {
        "rows": payload_rows,
        "footer": {"zhuangche_jieshu": len(payload_rows), "paiche_jieshu": 0},
        "project": "中唐特钢铁矿发运项目",
        "_agent_sop_authorized": authorized,
    }
    conn.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, actual_wagon_count
        ) VALUES ('batch-1', 'batch-key', '中唐特钢铁矿发运项目', '鞍子河', '铁矿', '沙子',
                  '2026-05-01', '2026-05-01', 'lot04', 140, '{}', '', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, group_name, release_batch_id, wagon_count, car_numbers_json, payload_json
        ) VALUES (?, 'inspection.jpg', 'candidate', 'matched_release_batch_waiting_95306_validation', '中唐特钢发运群', 'batch-1', ?, ?, ?)
        """,
        ("cand-1", len(payload_rows), json.dumps([r["car_no"] for r in payload_rows]), json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    _insert_shipments(
        rail_db,
        [
            ("yd1", "100001", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:00"),
            ("yd2", "100002", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:10"),
            ("yd3", "200001", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:20"),
        ],
    )
    return biz_db, rail_db


def _create_business_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE release_batches (
          id TEXT PRIMARY KEY,
          batch_key TEXT NOT NULL UNIQUE,
          project TEXT,
          ship_name TEXT NOT NULL,
          cargo_name TEXT NOT NULL,
          destination_station TEXT,
          notice_date TEXT NOT NULL,
          batch_date TEXT,
          batch_sequence TEXT,
          batch_quantity REAL,
          actual_wagon_count INTEGER DEFAULT 0,
          source_json TEXT NOT NULL,
          searchable_text TEXT NOT NULL,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE inspection_ingestion_candidates (
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
        );
        """
    )
    conn.commit()
    conn.close()


def _create_rail_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
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
        )
        """
    )
    _ensure_match_table(conn)
    conn.commit()
    conn.close()


def _insert_shipments(path: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    conn = sqlite3.connect(path)
    for ydid, car_no, car_model, cargo_name, destination, ticketed_at in rows:
        conn.execute(
            """
            INSERT INTO shipments (
              ydid, car_no, car_model, cargo_name, marked_weight, loaded_at, ticketed_at,
              origin_name, destination_name, transport_mode_name, status_code, status_name,
              latest_stage_name, latest_event_time
            ) VALUES (?, ?, ?, ?, '70.00', ?, ?, '高桥镇', ?, '整车运输', '80', '货物已交付', '交付', ?)
            """,
            (ydid, car_no, car_model, cargo_name, ticketed_at, ticketed_at, destination, ticketed_at),
        )
    conn.commit()
    conn.close()


def _match_count(path: Path, release_batch_id: str) -> int:
    conn = sqlite3.connect(path)
    count = conn.execute("select count(*) from shipment_release_batch_matches where release_batch_id=?", (release_batch_id,)).fetchone()[0]
    conn.close()
    return int(count)


def _matched_cars(path: Path, release_batch_id: str) -> list[str]:
    conn = sqlite3.connect(path)
    rows = conn.execute("select shipment_car_no from shipment_release_batch_matches where release_batch_id=? order by shipment_car_no", (release_batch_id,)).fetchall()
    conn.close()
    return [row[0] for row in rows]


def _business_actual_wagon_count(path: Path, release_batch_id: str) -> int:
    conn = sqlite3.connect(path)
    count = conn.execute("select actual_wagon_count from release_batches where id=?", (release_batch_id,)).fetchone()[0]
    conn.close()
    return int(count)
