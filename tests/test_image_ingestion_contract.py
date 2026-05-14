from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from ops_hub.config import Settings
from ops_hub.data_agent.agent import BusinessDataAgent
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
    assert Path(result.saved_path).parts[-7:] == ("朝阳钢铁铁矿发运项目", "朝阳西", "合远9", "lot01", "images", "2026-05-12", "departure.jpg")
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
        "project": "朝阳钢铁铁矿发运项目",
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

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
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


def test_sop_inspection_slip_with_release_batch_lands_candidate_row(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "inspection_with_release.jpg")
    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(test_db),
        test_agent_db_path=str(test_db),
        auto_extract_categories=["检装车通知单"],
    )
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(test_db))
    BusinessDataAgent().ingest_release_batch(
        {
            "is_target": True,
            "project": "朝阳钢铁铁矿发运项目",
            "header_info": {"通知日期": "2026-05-12"},
            "business_info": {"船名": "合远9", "发货单位": "测试发货方", "收货单位": "朝阳钢铁"},
            "cargo_info": {"货物名称": "铁矿粉", "总重里": "1000"},
            "special_matter": "到站：朝阳西",
            "remarks": [{"date": "2026-05-12", "sequence": "lot01", "quantity": 1000, "destination": "朝阳西"}],
        },
        source_file_name="seed_departure.jpg",
    )

    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
    inspection_payload = {
        "is_inspection": True,
        "project": "朝阳钢铁铁矿发运项目",
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
    assert extracted["_agent_candidate_ids"]
    assert extracted["_agent_pending_reason"] == "matched_release_batch_waiting_95306_validation"
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates where source_file_name=? and status='candidate' and release_batch_id is not null", ("inspection_with_release.jpg",)) == 1
    assert _count(test_db, "select count(*) from image_ingestion_audit where classified_category='检装车通知单' and db_action='candidate_pending' and reason='matched_release_batch_waiting_95306_validation'") == 1


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

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
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


def test_lobster_sandbox_infers_zt_departure_from_business_content_and_dedupes(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "anzihe.jpg")
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
        "header_info": {"通知日期": "2026年6月17日"},
        "business_info": {"船名": "鞍子河", "发货单位": "锦州港", "收货单位": "锦州新德物流有限公司"},
        "cargo_info": {"货物名称": "铁矿", "总重里": "41410"},
        "special_matter": "",
        "remarks": [
            {"date": "6月17日", "sequence": "第一次下达计划", "raw_line": "6月17日第一次下达计划: 10000吨（铁路 汐子）"},
            {"date": "6月18日", "sequence": "第二次下达计划", "raw_line": "6月18日第二次下达计划: 10000吨（铁路 汐子）"},
            {"date": "6月29日", "sequence": "第三次下达计划", "raw_line": "6月29日第三次下达计划: 10000吨（公路 赤峰中唐）"},
            {"date": "6月30日", "sequence": "第四次下达计划", "raw_line": "6月30日第四次下达计划: 11410吨（铁路 汐子 剩余20065吨）"},
        ],
    }

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
    ):
        first = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")
        second = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(second.extraction_saved_path).read_text(encoding="utf-8"))
    assert first.category == "出港计划通知单"
    assert extracted["project"] == "中唐特钢铁矿发运项目"
    assert extracted["_agent_project_inferred_from"] == "出港计划通知单"
    assert extracted["_agent_ingested"] == 4
    assert _count(test_db, "select count(*) from release_batches where source_file_name=?", ("anzihe.jpg",)) == 4
    assert _count(test_db, "select count(*) from release_batches where project='中唐特钢铁矿发运项目'") == 4


def test_lobster_sandbox_infers_zt_departure_from_fengshou_tuozi_ocr(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "fengshou_tuozi.jpg")
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
        "title": "锦州港货物出港计划通知单",
        "header_info": {"通知日期": "2026年05月10日", "内、外贸": "外贸"},
        "business_info": {"船名": "丰收散运", "发货单位": "锦州新德物流有限公司", "收货单位": "锦州新德物流有限公司"},
        "cargo_info": {"货物名称": "铁矿", "总重里": "10000", "运输方式": "铁路"},
        "special_matter": "",
        "remarks": [{"date": "5月10日", "sequence": "第一次下达计划", "plan": "10000吨（铁路 沱子）", "raw_line": "5月10日第一次下达计划：10000吨（铁路 沱子）"}],
    }

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["project"] == "中唐特钢铁矿发运项目"
    assert extracted["_agent_project_inferred_from"] == "出港计划通知单"
    assert extracted["_agent_ingested"] == 1
    assert _count(test_db, "select count(*) from release_batches where project='中唐特钢铁矿发运项目' and ship_name='丰收散运' and destination_station='汐子'") == 1
    assert _count(test_db, "select count(*) from release_dispatch_match_rules where project='中唐特钢铁矿发运项目' and ship_name='丰收散运' and destination_station='汐子' and status='active'") == 1


def test_lobster_sandbox_infers_chaoyang_departure_without_project_field(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    img = _make_image(tmp_path / "heyuan9.jpg")
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
        "header_info": {"通知日期": "2026年4月29日"},
        "business_info": {"船名": "合远9", "发货单位": "锦州港", "收货单位": "鞍钢汽车运输有限责任公司"},
        "cargo_info": {"货物名称": "铁矿", "总重里": "16345"},
        "special_matter": "火运敞车出港，到站：朝阳西",
        "remarks": [{"date": "4月29日", "sequence": "", "raw_line": "4月29日货主通知：火运敞车出港，到站：朝阳西，16345吨"}],
    }

    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.departure_plan.DeparturePlanEngine.process_image", return_value=departure_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["project"] == "朝阳钢铁铁矿发运项目"
    assert extracted["_agent_ingested"] == 1
    assert _count(test_db, "select count(*) from release_batches where project='朝阳钢铁铁矿发运项目' and ship_name='合远9'") == 1


def test_lobster_sandbox_inspection_without_project_routes_known_sop_destinations(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
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

    def run_case(name: str, rows: list[dict], expected_project: str) -> dict:
        img = _make_image(tmp_path / name)
        payload = {"is_inspection": True, "rows_count": len(rows), "rows": rows, "footer": {"zhuangche_jieshu": len(rows), "paiche_jieshu": 0}}
        with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
            "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=payload
        ):
            result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")
        extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
        assert extracted["project"] == expected_project
        assert extracted["_agent_candidate_ids"]
        return extracted

    chaoyang = run_case(
        "chaoyang_inspection.jpg",
        [{"seq": 1, "car_no": "4202018", "cargo_info_effective": "朝阳西铁矿粉 合远9", "defect": False}],
        "朝阳钢铁铁矿发运项目",
    )
    zt_pending = run_case(
        "xizi_pending.jpg",
        [{"seq": 1, "car_no": "1562661", "cargo_info_effective": "汐子铁矿粉", "defect": False}],
        "中唐特钢铁矿发运项目",
    )
    assert chaoyang["_agent_pending_reason"] == "no_release_batch_candidate"
    assert zt_pending["_agent_pending_reason"] == "no_release_batch_candidate"
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates") == 2


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
    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_sop_authorized"] is False
    assert extracted["_agent_sop_skip_reason"] == "non_sop_project_json_only"
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates") == 0


def test_xizi_inspection_without_ship_anchor_does_not_false_match_anzihe_release(tmp_path: Path, monkeypatch) -> None:
    test_db = tmp_path / "agent_test.db"
    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(test_db),
        test_agent_db_path=str(test_db),
        auto_extract_categories=["检装车通知单"],
    )
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(test_db))
    BusinessDataAgent().ingest_release_batch(
        {
            "is_target": True,
            "project": "中唐特钢铁矿发运项目",
            "header_info": {"通知日期": "2026年6月17日"},
            "business_info": {"船名": "鞍子河", "发货单位": "锦州港", "收货单位": "锦州新德物流有限公司"},
            "cargo_info": {"货物名称": "铁矿", "总重里": "10000"},
            "special_matter": "",
            "remarks": [{"date": "6月17日", "sequence": "第一次下达计划", "raw_line": "6月17日第一次下达计划: 10000吨（铁路 汐子）"}],
        },
        source_file_name="anzihe_departure.jpg",
    )

    img = _make_image(tmp_path / "xizi_bella_without_ship.jpg")
    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
    inspection_payload = {
        "is_inspection": True,
        "rows_count": 1,
        "rows": [{"seq": 1, "car_no": "1562661", "cargo_info_effective": "汐子铁矿粉", "defect": False}],
        "footer": {"zhuangche_jieshu": 59, "paiche_jieshu": 0},
    }
    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
    ):
        result = process_new_image(img, settings, month_str="202605", group_name="龙虾测试群")

    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["project"] == "中唐特钢铁矿发运项目"
    assert extracted["_agent_pending_reason"] == "no_release_batch_candidate"
    assert _count(test_db, "select count(*) from inspection_ingestion_candidates where status='pending' and release_batch_id is null") == 1

