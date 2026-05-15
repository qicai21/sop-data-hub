from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from ops_hub.config import Settings
from ops_hub.runner import process_new_image


def test_contextual_artifacts_and_status_file(tmp_path):
    img = Image.new("RGB", (32, 32), color="white")
    image_path = tmp_path / "123_abc.jpg"
    img.save(image_path)

    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        auto_extract_categories=["检装车通知单"],
    )

    cls_result = MagicMock(category="检装车通知单", confidence=0.95)
    classifier = MagicMock()
    classifier.classify.return_value = cls_result

    extracted = {
        "rows_count": 1,
        "rows": [{"car_no": "1234567"}],
        "footer": {"zhuangche_jieshu": 53},
        "_agent_updated_ids": ["batch-1"],
    }
    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.runner._run_extraction", return_value=extracted
    ):
        result = process_new_image(
            image_path,
            settings,
            month_str="2026-05",
            group_name="铁晟业务工作群",
        )

    base = tmp_path / "wechat_images"
    assert Path(result.saved_path) == base / "unmatched" / "2026-05" / "images" / "123_abc.jpg"
    assert Path(result.extraction_saved_path) == base / "unmatched" / "2026-05" / "json" / "123_abc_result.json"
    assert Path(result.status_path) == base / "铁晟业务工作群" / "_status" / "123_abc.json"
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    status_text = Path(result.status_path).read_text(encoding="utf-8")
    assert '"state": "extracted"' in status_text
    assert '"category": "检装车通知单"' in status_text
    assert '"rows_count": 1' in status_text
    assert '"_agent_updated_ids": [\n    "batch-1"\n  ]' in status_text
    assert str(Path(result.extraction_saved_path)) in status_text


def test_sop_artifacts_move_to_project_archive_and_reconcile_plan_is_recorded(tmp_path):
    import json
    import sqlite3

    img = Image.new("RGB", (32, 32), color="white")
    image_path = tmp_path / "inspection_plan.jpg"
    img.save(image_path)
    agent_db = tmp_path / "agent.db"
    rail_db = tmp_path / "rail.sqlite3"
    settings = Settings(
        classified_output_dir=str(tmp_path / "artifacts"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(agent_db),
        db_95306_path=str(rail_db),
        auto_extract_categories=["检装车通知单"],
    )

    import os
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(agent_db)
    from ops_hub.data_agent.agent import BusinessDataAgent

    release = BusinessDataAgent().ingest_release_batch(
        {
            "is_target": True,
            "project": "朝阳钢铁铁矿发运项目",
            "header_info": {"通知日期": "2026-05-12"},
            "business_info": {"船名": "合远9", "发货单位": "测试发货方", "收货单位": "朝阳钢铁"},
            "cargo_info": {"货物名称": "铁矿粉", "总重里": "140"},
            "special_matter": "到站：朝阳西",
            "remarks": [{"date": "2026-05-12", "sequence": "lot01", "quantity": 140, "destination": "朝阳西"}],
        },
        source_file_name="departure.jpg",
    )[0]
    conn = sqlite3.connect(rail_db)
    conn.execute(
        """CREATE TABLE shipments (
        ydid TEXT PRIMARY KEY, car_no TEXT, car_model TEXT, marked_weight REAL, loaded_at TEXT, ticketed_at TEXT,
        origin_name TEXT, destination_name TEXT, cargo_name TEXT, transport_mode_name TEXT, status_code TEXT, status_name TEXT,
        latest_stage_name TEXT, latest_event_time TEXT)"""
    )
    conn.executemany(
        "INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("yd1", "100001", "C70", 70, "2026-05-12", "2026-05-12 10:00:00", "锦州港", "朝阳西", "铁矿粉", "铁路", "", "", "", ""),
            ("yd2", "100002", "C70", 70, "2026-05-12", "2026-05-12 10:01:00", "锦州港", "朝阳西", "铁矿粉", "铁路", "", "", "", ""),
        ],
    )
    conn.commit(); conn.close()

    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
    inspection_payload = {
        "is_inspection": True,
        "project": "朝阳钢铁铁矿发运项目",
        "meta": {"date": "2026-05-12"},
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
        result = process_new_image(image_path, settings, month_str="202605", group_name="龙虾测试群")

    assert Path(result.saved_path) == tmp_path / "artifacts" / "projects" / "朝阳钢铁铁矿发运项目" / "朝阳西" / "合远9" / "lot01" / "images" / "2026-05-12" / "inspection_plan.jpg"
    assert Path(result.extraction_saved_path) == tmp_path / "artifacts" / "projects" / "朝阳钢铁铁矿发运项目" / "朝阳西" / "合远9" / "lot01" / "json" / "2026-05-12" / "inspection_plan_result.json"
    extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
    assert extracted["_agent_reconcile_plan"]["safe_to_commit"] is True
    assert extracted["_agent_reconcile_plan"]["planned_write_count"] == 2
    assert extracted["_agent_reconcile_plan"]["committed_count"] == 0
    status = json.loads(Path(result.status_path).read_text(encoding="utf-8"))
    assert status["reconcile_plan"]["planned_write_count"] == 2
    assert status["safe_to_commit"] is True
    assert status["requires_manual_review"] is False
    assert status["planned_write_count"] == 2
    assert status["excluded_count"] == 0
    assert status["project_archive_paths"]["image"] == result.saved_path
    assert status["project_archive_paths"]["json"] == result.extraction_saved_path
    conn = sqlite3.connect(rail_db)
    try:
        assert conn.execute("select count(*) from shipment_release_batch_matches").fetchone()[0] == 0
    finally:
        conn.close()
    conn = sqlite3.connect(agent_db)
    try:
        audit = conn.execute("select safe_to_commit, requires_manual_review, planned_write_count, excluded_count, project_archive_paths from image_ingestion_audit where classified_category='检装车通知单'").fetchone()
        assert audit == (1, 0, 2, 0, json.dumps({"image": result.saved_path, "json": result.extraction_saved_path}, ensure_ascii=False))
    finally:
        conn.close()


def test_non_sop_artifacts_go_to_unmatched_without_second_extraction(tmp_path):
    import json

    img = Image.new("RGB", (32, 32), color="white")
    image_path = tmp_path / "unknown.jpg"
    img.save(image_path)
    settings = Settings(
        classified_output_dir=str(tmp_path / "artifacts"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(tmp_path / "agent.db"),
        auto_extract_categories=["检装车通知单"],
    )
    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
    calls = {"count": 0}
    def fake_extract(*args, **kwargs):
        calls["count"] += 1
        return {"is_inspection": True, "project": "未登记测试项目", "meta": {"date": "2026-05-12"}, "rows": [], "_agent_sop_authorized": False, "_agent_sop_skip_reason": "non_sop_project_json_only"}
    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch("ops_hub.runner._run_extraction", side_effect=fake_extract):
        result = process_new_image(image_path, settings, month_str="202605", group_name="龙虾测试群")

    assert calls["count"] == 1
    assert Path(result.saved_path) == tmp_path / "artifacts" / "unmatched" / "2026-05" / "images" / "unknown.jpg"
    assert Path(result.extraction_saved_path) == tmp_path / "artifacts" / "unmatched" / "2026-05" / "json" / "unknown_result.json"
    status = json.loads(Path(result.status_path).read_text(encoding="utf-8"))
    assert status["project_archive_paths"]["image"] == result.saved_path
    assert status["project_archive_paths"]["json"] == result.extraction_saved_path


def test_ambiguous_inspection_artifacts_move_to_pending_lot_not_candidate_lot(tmp_path):
    import json
    import os
    import sqlite3

    from ops_hub.data_agent.agent import BusinessDataAgent

    img = Image.new("RGB", (32, 32), color="white")
    image_path = tmp_path / "malan_inspection.jpg"
    img.save(image_path)
    agent_db = tmp_path / "agent.db"
    settings = Settings(
        classified_output_dir=str(tmp_path / "artifacts"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        agent_db_path=str(agent_db),
        auto_extract_categories=["检装车通知单"],
    )
    os.environ["BUSINESS_DATA_AGENT_DB_PATH"] = str(agent_db)
    try:
        agent = BusinessDataAgent()
        for seq in ["lot01", "lot04"]:
            batch_id = f"malan-{seq}"
            agent.db.execute(
                """
                INSERT INTO release_batches (
                  id, batch_key, project, ship_name, cargo_name, destination_station,
                  notice_date, batch_date, batch_sequence, batch_quantity,
                  batch_count, source_json, searchable_text
                ) VALUES (?, ?, '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子',
                          '2026-05-13', '2026-05-13', ?, 10000, 1, '{}', ?)
                """,
                (batch_id, f"malan|xizi|{seq}", seq, f"马兰探险 汐子 铁矿 {seq}"),
            )
        agent.db.commit()
        agent.refresh_release_dispatch_match_rules()

        classifier = MagicMock()
        classifier.classify.return_value = MagicMock(category="检装车通知单", confidence=0.96)
        inspection_payload = {
            "is_inspection": True,
            "project": "朝阳钢铁铁矿发运项目",
            "ship_name": "马兰探险",
            "destination_station": "汐子",
            "meta": {"date": "2026-05-13"},
            "rows_count": 1,
            "rows": [{"seq": 1, "car_no": "300001", "cargo_info_effective": "汐子铁矿粉/马兰探险"}],
            "cargo_summary": {"汐子铁矿粉/马兰探险": ["300001"]},
            "footer": {"zhuangche_jieshu": 1, "paiche_jieshu": 0},
        }
        with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
            "ops_hub.engines.inspection_slip.InspectionSlipEngine.process_image", return_value=inspection_payload
        ):
            result = process_new_image(image_path, settings, month_str="202605", group_name="数据单发群-GROUP013")

        assert Path(result.saved_path) == tmp_path / "artifacts" / "projects" / "中唐特钢铁矿发运项目" / "汐子" / "马兰探险" / "pending_lot" / "images" / "2026-05-13" / "malan_inspection.jpg"
        assert Path(result.extraction_saved_path) == tmp_path / "artifacts" / "projects" / "中唐特钢铁矿发运项目" / "汐子" / "马兰探险" / "pending_lot" / "json" / "2026-05-13" / "malan_inspection_result.json"
        assert "lot01" not in str(result.saved_path)
        assert "lot04" not in str(result.saved_path)
        extracted = json.loads(Path(result.extraction_saved_path).read_text(encoding="utf-8"))
        assert extracted["_agent_status"] == "ambiguous"
        assert extracted["_agent_updated_ids"] == []
        assert sorted(extracted["_agent_candidate_release_batch_ids"]) == ["malan-lot01", "malan-lot04"]
        conn = sqlite3.connect(agent_db)
        try:
            row = conn.execute("SELECT payload_json FROM inspection_ingestion_candidates WHERE source_file_name='malan_inspection.jpg'").fetchone()
            candidate_payload = json.loads(row[0])
            assert sorted(candidate_payload["_candidate_release_batch_ids"]) == ["malan-lot01", "malan-lot04"]
        finally:
            conn.close()
    finally:
        os.environ.pop("BUSINESS_DATA_AGENT_DB_PATH", None)
