from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from sop_hub.config import Settings
from sop_hub.data_agent.agent import BusinessDataAgent
from sop_hub.runner import process_new_image


def _make_image(path: Path) -> Path:
    Image.new("RGB", (24, 24), color="white").save(path)
    return path


def _count(db_path: Path, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()





def test_non_sop_departure_plan_extracts_json_without_db_landing(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "non_sop_departure.jpg")
    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(test_db),
        test_agent_db_path=str(test_db),
        auto_extract_categories=["出港计划通知单"],
    )
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(test_db))

    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="出港计划通知单", confidence=0.95)
    departure_payload = {
        "is_target": True,
        "title": "出港计划通知单",
        "project": "未登记测试项目",
        "header_info": {"通知日期": "2026-05-12"},
        "business_info": {"船名": "测试船", "发货单位": "测试发货方", "收货单位": "测试收货方"},
        "cargo_info": {"货物名称": "铁矿粉", "总重里": "1000"},
        "special_matter": "到站：测试站",
        "remarks": [{"date": "2026-05-12", "sequence": "lot01", "quantity": 1000, "destination": "测试站"}],
    }

    with patch("sop_hub.runner._get_classifier", return_value=classifier), patch(
        "sop_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_sop_authorized"] is False
    assert extracted["_agent_sop_skip_reason"] == "non_sop_project_json_only"
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    assert Path(result.status_path).exists()
    assert _count(test_db, "select count(*) from sqlite_master where type='table' and name='release_batches'") == 1
    assert _count(test_db, "select count(*) from release_batches") == 0
    assert _count(test_db, "select count(*) from image_ingestion_audit where db_action='none' and reason='non_sop_project_json_only'") == 1




def test_non_sop_inspection_slip_extracts_json_without_candidate(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "non_sop_inspection.jpg")
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
        "project": "未登记测试项目",
        "rows_count": 1,
        "rows": [{"seq": 1, "car_no": "200001", "car_type": "70", "cargo_info_effective": "测试站铁矿粉 测试船", "defect": False}],
        "footer": {"zhuangche_jieshu": 1, "paiche_jieshu": 0},
    }

    with patch("sop_hub.runner._get_classifier", return_value=classifier), patch(
        "sop_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_sop_authorized"] is False
    assert extracted["_agent_sop_skip_reason"] == "non_sop_project_json_only"
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    assert Path(result.status_path).exists()
    assert _count(test_db, "select count(*) from sqlite_master where type='table' and name='inspection_ingestion_candidates'") == 1
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates") == 0
    assert _count(test_db, "select count(*) from image_ingestion_audit where db_action='none' and reason='non_sop_project_json_only'") == 1












def test_lobster_sandbox_non_sop_lingdong_kadi_stays_json_only(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "lingdong_kadi.jpg")
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
        "rows_count": 1,
        "rows": [{"seq": 1, "car_no": "1705404", "cargo_info_effective": "凌源东铁矿粉 卡迪", "defect": False}],
        "footer": {"zhuangche_jieshu": 64, "paiche_jieshu": 0},
    }
    with patch("sop_hub.runner._get_classifier", return_value=classifier), patch(
        "sop_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_sop_authorized"] is False
    assert extracted["_agent_sop_skip_reason"] == "non_sop_project_json_only"
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates") == 0


