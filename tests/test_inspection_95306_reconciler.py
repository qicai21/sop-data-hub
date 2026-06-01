from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from sop_hub.data_agent.agent import BusinessDataAgent
from sop_hub.matching.inspection_95306_reconciler import reconcile_inspection_shipments
from sop_hub.matching.shipment_linkage import _ensure_match_table


def test_candidate_status_does_not_write_formal_table_until_commit(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)

    before = _match_count(rail_db, "batch-1")
    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="plan",
        operator_note="pytest plan",
    )

    assert before == 0
    assert result.safe_to_commit is True
    report = result.to_report_dict()
    assert report["run_mode"] == "plan"
    assert "reconcile_plan" in report
    assert report["planned_write_count"] == 2
    assert report["matched_95306_count"] == 2
    assert report["safe_to_commit"] is True
    assert report["requires_manual_review"] is False
    assert result.planned_write_count == 2
    assert _match_count(rail_db, "batch-1") == 0


def test_commit_writes_matches_and_repeated_commit_is_idempotent(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)

    first = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest commit",
    )
    second = reconcile_inspection_shipments(
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

    result = reconcile_inspection_shipments(
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

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest unauthorized",
    )

    assert result.safe_to_commit is False
    assert "candidate-sop-not-authorized" in result.review_reasons
    assert _match_count(rail_db, "batch-1") == 0


def test_defect_rows_are_excluded_before_window_count_and_linkage(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    payload = {
        "rows": [
            {"seq": 1, "car_no": "100001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉", "defect": False},
            {"seq": 2, "car_no": "100002", "car_type": "70", "cargo_info_raw": "鞍子河", "cargo_info_effective": "汐子铁矿粉/鞍子河", "defect": False},
            {"seq": 3, "car_no": "100003", "car_type": "70", "cargo_info_raw": "临修", "cargo_info_effective": "汐子铁矿粉/临修", "defect": True},
        ],
        "footer": {"zhuangche_jieshu": 3, "paiche_jieshu": 1},
        "project": "中唐特钢铁矿发运项目",
        "_agent_sop_authorized": True,
    }
    with sqlite3.connect(biz_db) as conn:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET wagon_count=3, car_numbers_json=?, payload_json=? WHERE id='cand-1'",
            (json.dumps(["100001", "100002", "100003"]), json.dumps(payload, ensure_ascii=False)),
        )

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest defect rows excluded first",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 2
    assert _match_count(rail_db, "batch-1") == 2
    assert _matched_cars(rail_db, "batch-1") == ["100001", "100002"]
    assert not any(item["reason"] == "95306-window-count-mismatch" for item in result.excluded)


def test_footer_boundary_defect_on_loading_limit_is_kept_when_trailing_paiche_exists(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    payload = {
        "rows_count": 46,
        "rows": [
            {"seq": 44, "car_no": "100001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉/鞍子河", "defect": False},
            {"seq": 45, "car_no": "100002", "car_type": "70", "cargo_info_raw": "鞍子河", "cargo_info_effective": "汐子铁矿粉/鞍子河", "remark": "双划不入槽", "defect": True},
            {"seq": 46, "car_no": "100003", "car_type": "70", "cargo_info_raw": "", "cargo_info_effective": "汐子铁矿粉/鞍子河", "remark": "双划不入槽", "defect": True},
        ],
        "footer": {"zhuangche_jieshu": 45, "paiche_jieshu": 1},
        "project": "中唐特钢铁矿发运项目",
        "_agent_sop_authorized": True,
    }
    with sqlite3.connect(biz_db) as conn:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET wagon_count=3, car_numbers_json=?, payload_json=? WHERE id='cand-1'",
            (json.dumps(["100001", "100002", "100003"]), json.dumps(payload, ensure_ascii=False)),
        )

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest footer boundary defect correction",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 2
    assert _matched_cars(rail_db, "batch-1") == ["100001", "100002"]
    assert any(item["wagon_no"] == "100003" and item["reason"] == "ocr-defect" for item in result.excluded)


def test_footer_boundary_defect_on_manual_split_row_end_is_kept(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    payload = {
        "rows_count": 2,
        "rows": [
            {"seq": 44, "car_no": "100001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉/鞍子河", "defect": False},
            {"seq": 45, "car_no": "100002", "car_type": "70", "cargo_info_raw": "鞍子河", "cargo_info_effective": "汐子铁矿粉/鞍子河", "remark": "双划不入槽", "defect": True},
        ],
        "footer": {"zhuangche_jieshu": 45, "paiche_jieshu": 1},
        "_manual_assignment": {"row_start": 44, "row_end": 45, "lot": "lot04"},
        "project": "中唐特钢铁矿发运项目",
        "_agent_sop_authorized": True,
    }
    with sqlite3.connect(biz_db) as conn:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET wagon_count=2, car_numbers_json=?, payload_json=? WHERE id='cand-1'",
            (json.dumps(["100001", "100002"]), json.dumps(payload, ensure_ascii=False)),
        )

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest split footer boundary defect correction",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 2
    assert _matched_cars(rail_db, "batch-1") == ["100001", "100002"]


def test_footer_confirmed_loaded_segment_trusts_db_window_when_ocr_marks_inner_row_defect(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    payload = {
        "rows_count": 3,
        "rows": [
            {"seq": 1, "car_no": "100001", "car_type": "70", "cargo_info_raw": "汐子铁矿粉", "cargo_info_effective": "汐子铁矿粉", "defect": False},
            {"seq": 2, "car_no": "100099", "car_type": "70", "cargo_info_raw": "鞍子河 3节 临修", "cargo_info_effective": "汐子铁矿粉/鞍子河 3节 临修", "defect": True},
            {"seq": 3, "car_no": "100003", "car_type": "70", "cargo_info_raw": "双划不入槽", "cargo_info_effective": "汐子铁矿粉/双划不入槽", "defect": False},
        ],
        "footer": {"zhuangche_jieshu": 3, "paiche_jieshu": 0},
        "project": "中唐特钢铁矿发运项目",
        "_agent_sop_authorized": True,
    }
    with sqlite3.connect(biz_db) as conn:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET wagon_count=3, car_numbers_json=?, payload_json=? WHERE id='cand-1'",
            (json.dumps(["100001", "100099", "100003"]), json.dumps(payload, ensure_ascii=False)),
        )
    _insert_shipments(rail_db, [("yd3", "100003", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:20")])

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest footer confirmed loaded segment",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 3
    assert _match_count(rail_db, "batch-1") == 3
    assert _business_actual_wagon_count(biz_db, "batch-1") == 3
    assert not result.review_reasons


def test_anzihe_lot04_scope_is_fixed_when_live_databases_exist() -> None:
    biz_db = Path("/Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db")
    rail_db = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
    if not biz_db.exists() or not rail_db.exists():
        pytest.skip("machine-local Anzihe lot04 databases are not present")

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="88ceb9b2086fed6e81cd4eadb8b2b0a0002c8c01",
        run_mode="plan",
        operator_note="pytest anzihe lot04 plan",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 164
    assert result.matched_95306_count == 164


def test_plan_requires_manual_review_when_95306_window_has_unmatched_extra_row(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    _insert_shipments(
        rail_db,
        [("yd-extra", "999999", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:30")],
    )

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="plan",
        operator_note="pytest unsafe plan",
    )

    assert result.safe_to_commit is False
    assert result.requires_manual_review is True
    assert "95306-window-count-mismatch" in result.review_reasons
    assert _match_count(rail_db, "batch-1") == 0


def test_commit_refuses_unsafe_plan_without_manual_override(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    _insert_shipments(
        rail_db,
        [("yd-extra", "999999", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:30")],
    )

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="commit",
        operator_note="pytest unsafe commit",
    )

    assert result.safe_to_commit is False
    assert result.committed_count == 0
    assert _match_count(rail_db, "batch-1") == 0


def test_manual_assigned_candidate_can_generate_reconciliation_plan(tmp_path: Path) -> None:
    biz_db, rail_db = _build_fixture(tmp_path, authorized=True)
    with sqlite3.connect(biz_db) as conn:
        conn.execute(
            "UPDATE inspection_ingestion_candidates SET status='ambiguous', reason='ambiguous_release_batch_candidate', release_batch_id=NULL WHERE id='cand-1'"
        )
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(biz_db)
    try:
        agent = BusinessDataAgent()
        assert agent.assign_inspection_candidate("cand-1", "batch-1", operator_note="pytest manual assign") is True
    finally:
        os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)

    result = reconcile_inspection_shipments(
        business_db_path=biz_db,
        rail_db_path=rail_db,
        project_id="中唐特钢铁矿发运项目",
        release_batch_id="batch-1",
        candidate_ids=["cand-1"],
        run_mode="plan",
        operator_note="pytest plan after manual assign",
    )

    assert result.safe_to_commit is True
    assert result.planned_write_count == 2
    assert _match_count(rail_db, "batch-1") == 0



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

    shipment_rows = [
        ("yd1", "100001", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:00"),
        ("yd2", "100002", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:10"),
    ]
    if include_other_ship_segment:
        shipment_rows.append(("yd3", "200001", "C70", "铁矿粉", "汐子", "2026-05-01 08:00:20"))
    _insert_shipments(rail_db, shipment_rows)
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
