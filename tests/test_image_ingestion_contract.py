from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from ops_hub.config import Settings
from ops_hub.runner import process_new_image


def _make_image(path: Path) -> Path:
    Image.new("RGB", (24, 24), color="white").save(path)
    return path


def _count(db_path: Path, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def test_lobster_group_departure_plan_lands_files_audit_and_test_db(tmp_path: Path, monkeypatch) -> None:
    prod_db = tmp_path / "prod_agent.db"
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "departure.jpg")
    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(test_db),
        test_agent_db_path=str(test_db),
        auto_extract_categories=["出港计划通知单"],
    )
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(prod_db))

    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="出港计划通知单", confidence=0.97)
    departure_payload = {
        "is_target": True,
        "title": "出港计划通知单",
        "project": "朝阳钢铁铁矿发运项目",
        "header_info": {"通知日期": "2026-05-12", "入场计划号": "P-TEST"},
        "business_info": {"船名": "合远9", "发货单位": "测试发货方", "收货单位": "朝阳钢铁"},
        "cargo_info": {"货物名称": "铁矿粉", "总重里": "1000", "发货站(地)": "锦州港"},
        "special_matter": "到站：朝阳西",
        "remarks": [{"date": "2026-05-12", "sequence": "lot01", "quantity": 1000, "transport_mode": "铁路", "destination": "朝阳西", "raw_line": "第一次铁路朝阳西1000吨"}],
    }

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    assert result.category == "出港计划通知单"
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    assert Path(result.status_path).exists()
    assert Path(result.saved_path).parts[-3:] == ("龙虾测试群", "出港计划通知单", "departure.jpg")
    assert json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))["_agent_ingested"] == 1
    assert _count(test_db, "select count(*) from release_batches where source_file_name=?", ("departure.jpg",)) == 1
    assert not prod_db.exists() or _count(prod_db, "select count(*) from sqlite_master where type='table' and name='release_batches'") == 0
    assert _count(test_db, "select count(*) from image_ingestion_audit where group_name='龙虾测试群' and classified_category='出港计划通知单' and db_action='release_batch_upsert'") == 1


def test_inspection_slip_creates_candidate_when_release_batch_missing(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "inspection.jpg")
    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(test_db),
        test_agent_db_path=str(test_db),
        auto_extract_categories=["检装车通知单"],
    )
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(test_db))

    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
    inspection_payload = {
        "is_inspection": True,
        "rows_count": 2,
        "rows": [
            {"seq": 1, "car_no": "100001", "car_type": "70", "cargo_info_effective": "朝阳西铁矿粉 合远9", "defect": False},
            {"seq": 2, "car_no": "100002", "car_type": "70", "cargo_info_effective": "朝阳西铁矿粉 合远9", "defect": False},
        ],
        "footer": {"zhuangche_jieshu": 2, "paiche_jieshu": 0},
    }

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_pending_reason"] == "no_release_batch_candidate"
    assert extracted["_agent_candidate_ids"]
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates where source_file_name=? and status='pending'", ("inspection.jpg",)) == 1
    assert _count(test_db, "select count(*) from image_ingestion_audit where group_name='龙虾测试群' and classified_category='检装车通知单' and db_action='candidate_pending'") == 1
