from __future__ import annotations

import sqlite3
from pathlib import Path

from ops_hub.data_agent.agent import BusinessDataAgent
from ops_hub.data_agent.dispatch_board import render_dispatch_board


def _create_rail_db(path: Path, release_batch_id: str = "batch-1") -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE shipment_release_batch_matches (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT,
          planned_weight REAL,
          marked_weight REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO shipment_release_batch_matches (id, release_batch_id, planned_weight, marked_weight) VALUES ('m1', ?, 70.0, 70.0)",
        (release_batch_id,),
    )
    conn.execute(
        "INSERT INTO shipment_release_batch_matches (id, release_batch_id, planned_weight, marked_weight) VALUES ('m2', ?, 69.5, 70.0)",
        (release_batch_id,),
    )
    conn.commit()
    conn.close()


def test_render_dispatch_board_generates_html_with_release_candidate_and_formal_summary(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, cargo_product_name,
          destination_station, notice_date, batch_date, batch_sequence, batch_quantity,
          source_file_name, source_json, searchable_text, dispatch_status, plan_id, contract_no
        ) VALUES (
          'batch-1', 'project|ship|lot01', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '麦克粉',
          '汐子', '2026-05-10', '2026-05-10', 'lot01', 8248,
          '/tmp/malan.jpg', '{"image_path":"/tmp/malan.jpg","json_path":"/tmp/malan_result.json","status_path":"/tmp/malan_status.json"}',
          '马兰探险 汐子 铁矿', 'in_progress', '90260400079', 'ZLZT-2026042401'
        )
        """
    )
    agent.db.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, release_batch_id, wagon_count, car_numbers_json, payload_json
        ) VALUES ('cand-1', '/tmp/inspect.json', 'candidate', 'matched_release_batch_waiting_95306_validation', 'batch-1', 2, '["1","2"]', '{}')
        """
    )
    agent.db.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, release_batch_id, wagon_count, car_numbers_json, payload_json
        ) VALUES ('cand-committed', '/tmp/inspect-committed.json', 'committed', 'formal_95306_linkage_committed', 'batch-1', 2, '["4","5"]', '{}')
        """
    )
    agent.db.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, release_batch_id, wagon_count, car_numbers_json, payload_json
        ) VALUES ('cand-2', '/tmp/manual.json', 'ambiguous', 'ambiguous_release_batch_candidate', NULL, 1, '["3"]', ?)
        """,
        ('{"_candidate_release_batch_ids":["batch-1","batch-2"]}',),
    )
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, cargo_product_name,
          destination_station, notice_date, batch_date, batch_sequence, batch_quantity,
          source_file_name, source_json, searchable_text, dispatch_status
        ) VALUES (
          'batch-2', 'project|ship|lot04', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '铁矿粉',
          '汐子', '2026-05-10', '2026-05-10', 'lot04', 8248,
          '/tmp/malan2.jpg', '{}', '马兰探险 汐子 铁矿', 'in_progress'
        )
        """
    )
    agent.db.execute(
        """
        INSERT INTO image_ingestion_audit (
          id, raw_image_path, classified_image_path, extraction_json_path, project_archive_paths, status
        ) VALUES ('audit-1', '/tmp/raw.jpg', '/tmp/malan.jpg', '/tmp/malan_result.json', '{"status_path":"/tmp/malan_status.json"}', 'ok')
        """
    )
    agent.db.commit()
    rail_db = tmp_path / "rail.sqlite3"
    _create_rail_db(rail_db)
    output = tmp_path / "board.html"

    result = render_dispatch_board(business_db_path=tmp_db, rail_db_path=rail_db, output_path=output)

    html = output.read_text(encoding="utf-8")
    assert result["output_path"] == str(output)
    assert "release_batch" in html
    assert "candidate" in html
    assert "正式匹配汇总" in html
    assert "马兰探险" in html
    assert "当前发运中批次数" in html
    assert "已正式入库车数" in html
    assert "2" in html
    assert "139.5" in html
    assert "/tmp/malan.jpg" in html
    assert "/tmp/malan_result.json" in html
    assert "/tmp/malan_status.json" in html
    assert "候选 lot 列表" in html
    assert "lot01" in html
    assert "lot04" in html
    assert "麦克粉" in html
    assert "90260400079" in html
    assert "ZLZT-2026042401" in html
    assert "计划号/订单号" in html
    assert "合同号" in html
    assert "release_batch_id" not in html
    assert "batch-1" not in html
    assert "打开图片" in html
    assert "file:///tmp/malan.jpg" in html
    assert "打开JSON" in html
    assert "file:///tmp/malan_result.json" in html
    assert result["candidate_count"] == 2


def test_render_dispatch_board_derives_status_path_from_audit_image_path(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    group_dir = tmp_path / "数据单发群-GROUP013"
    image_dir = group_dir / "出港计划通知单"
    status_dir = group_dir / "_status"
    image_dir.mkdir(parents=True)
    status_dir.mkdir(parents=True)
    image_path = image_dir / "22_abc.jpg"
    status_path = status_dir / "22_abc.json"
    image_path.write_text("image", encoding="utf-8")
    status_path.write_text("{}", encoding="utf-8")
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity,
          source_file_name, source_json, searchable_text, dispatch_status
        ) VALUES (
          'batch-status', 'project|ship|lot01', '中唐特钢铁矿发运项目', '丰收散运', '铁矿', '汐子',
          '2026-05-10', '2026-05-10', 'lot01', 10000,
          '22_abc.jpg', '{}', '丰收散运 汐子 铁矿', 'in_progress'
        )
        """
    )
    agent.db.execute(
        """
        INSERT INTO image_ingestion_audit (
          id, raw_image_path, classified_image_path, extraction_json_path, project_archive_paths, status
        ) VALUES ('audit-status', NULL, ?, NULL, NULL, 'ok')
        """,
        (str(image_path),),
    )
    agent.db.commit()

    render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    assert str(status_path) in html


def test_render_dispatch_board_groups_rows_by_project(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    rows = [
        ("zt-old", "中唐特钢铁矿发运项目", "中唐旧船", "lot01", "2026-05-10"),
        ("wg-mid", "乌兰浩特钢铁铁矿发运项目", "乌钢中间船", "lot05", "2026-05-11"),
        ("zt-new", "中唐特钢铁矿发运项目", "鞍子河", "lot02", "2026-05-12"),
    ]
    for row_id, project, ship_name, lot, batch_date in rows:
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, project, ship_name, cargo_name, destination_station,
              notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
            ) VALUES (?, ?, ?, ?, '铁矿', '汐子', ?, ?, ?, 10000, '{}', ?, 'in_progress')
            """,
            (row_id, f"{project}|{ship_name}|{lot}", project, ship_name, batch_date, batch_date, lot, f"{project} {ship_name} {lot}"),
        )
    agent.db.commit()

    render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    zt_old = html.index("中唐旧船")
    wg_mid = html.index("乌钢中间船")
    zt_new = html.index("鞍子河")
    assert min(zt_old, zt_new) < wg_mid
    assert max(zt_old, zt_new) < wg_mid


def test_render_dispatch_board_separates_in_progress_and_completed_tables(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    rows = [
        ("running", "马兰探险", "lot07", "in_progress"),
        ("done", "贝拉", "lot01", "completed"),
    ]
    for row_id, ship_name, lot, status in rows:
        agent.db.execute(
            """
            INSERT INTO release_batches (
              id, batch_key, project, ship_name, cargo_name, destination_station,
              notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
            ) VALUES (?, ?, '中唐特钢铁矿发运项目', ?, '铁矿', '汐子',
              '2026-05-10', '2026-05-10', ?, 10000, '{}', ?, ?)
            """,
            (row_id, f"中唐特钢铁矿发运项目|{ship_name}|{lot}", ship_name, lot, f"{ship_name} {lot}", status),
        )
    agent.db.execute(
        """
        INSERT INTO image_ingestion_audit (
          id, message_type, raw_image_path, classified_category, db_action, db_tables,
          db_record_ids, status, reason, requires_manual_review
        ) VALUES (
          'text-pending', 'text', '船名：马兰探险\n数量：8248', '文字放货指令',
          'manual_match_pending', '["release_batches"]', '["running", "done"]',
          'pending', 'ambiguous_release_batch_match', 1
        )
        """
    )
    agent.db.commit()

    result = render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    running_title = html.index("发运中 release_batch 明细")
    completed_title = html.index("已发完 release_batch 明细")
    malan = html.index("马兰探险", running_title)
    bella = html.index("贝拉", completed_title)
    assert running_title < malan < completed_title < bella
    assert result["manual_pending_candidate_count"] == 2
    assert "待人工候选数" in html
    assert "待人工匹配文字放货消息" in html
    assert "待落实消息汇总（优先处理）" in html
    assert "待匹配文字放货消息" in html
    assert result["unresolved_total_count"] == 1
    assert result["pending_text_release_count"] == 1
    assert "8248" in html
    assert "lot07" in html


def test_render_dispatch_board_shows_top_unresolved_departure_and_inspection_counts(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
        ) VALUES ('batch-fs', 'zt|丰收散运|lot01', '中唐特钢铁矿发运项目', '丰收散运', '铁矿', '汐子',
          '2026-05-10', '2026-05-10', 'lot01', 10000, '{}', '丰收散运 汐子 铁矿', 'in_progress')
        """
    )
    raw_plan_path = tmp_path / "数据单发群-GROUP013" / "2026-05" / "fengshou_plan.jpg"
    preview_plan_path = raw_plan_path.parent / "_previews" / raw_plan_path.name
    preview_plan_path.parent.mkdir(parents=True, exist_ok=True)
    preview_plan_path.write_bytes(b"preview")
    classified_plan_path = tmp_path / "数据单发群-GROUP013" / "出港计划通知单" / raw_plan_path.name
    classified_plan_path.parent.mkdir(parents=True, exist_ok=True)
    classified_plan_path.write_bytes(b"classified")
    agent.db.execute(
        """
        INSERT INTO image_ingestion_audit (
          id, message_type, raw_image_path, classified_image_path, classified_category, db_action, status, reason, requires_manual_review,
          extraction_json_path
        ) VALUES (
          'plan-pending', 'image', ?, ?, '出港计划通知单', 'none', 'extracted',
          'non_sop_project_json_only', 0, '/tmp/fengshou_plan_result.json'
        )
        """,
        (str(raw_plan_path), str(classified_plan_path)),
    )
    agent.db.execute(
        """
        INSERT INTO image_ingestion_audit (
          id, message_type, raw_image_path, classified_category, db_action, db_record_ids, status, reason,
          extraction_json_path
        ) VALUES (
          'inspect-validation', 'image', '/tmp/fengshou_inspect.jpg', '检装车通知单', 'candidate_pending', '["batch-fs"]',
          'pending', 'matched_release_batch_waiting_95306_validation', '/tmp/fengshou_inspect_result.json'
        )
        """
    )
    agent.db.execute(
        """
        INSERT INTO inspection_ingestion_candidates (
          id, source_file_name, status, reason, release_batch_id, wagon_count, car_numbers_json, payload_json
        ) VALUES ('inspect-unassigned', '/tmp/no_batch_inspect.jpg', 'pending', 'no_release_batch_candidate', NULL, 56, '[]', '{"ship_name":"丰收散运"}')
        """
    )
    agent.db.commit()

    result = render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    assert result["pending_departure_plan_count"] == 1
    assert result["pending_inspection_assignment_count"] == 1
    assert result["pending_inspection_validation_count"] == 1
    assert result["unresolved_total_count"] == 3
    assert "待匹配出港计划/放货图片" in html
    assert "待指认检装车通知单" in html
    assert "待95306校验装车候选" in html
    assert "non_sop_project_json_only" in html
    assert "no_release_batch_candidate" in html
    assert "matched_release_batch_waiting_95306_validation" in html
    assert str(preview_plan_path) in html
    assert str(raw_plan_path) not in html
    assert str(classified_plan_path) not in html


def test_render_dispatch_board_filters_out_non_sop_release_batches(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
        ) VALUES ('sop-batch', 'sop|ship|lot01', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子',
          '2026-05-10', '2026-05-10', 'lot01', 10000, '{}', '马兰探险 汐子 铁矿', 'in_progress')
        """
    )
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
        ) VALUES ('non-sop-batch', 'non|ship|lot02', NULL, '卡迪', '铁矿', '凌源东（凌东）',
          '2026-05-12', '2026-05-12', 'lot02', 20000, '{}', '卡迪 凌源东 铁矿', 'in_progress')
        """
    )
    agent.db.commit()

    result = render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    assert result["release_batch_count"] == 1
    assert "马兰探险" in html
    assert "卡迪" not in html
    assert "凌源东" not in html


def test_render_dispatch_board_shows_clickable_car_detail_columns(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_date, batch_sequence, batch_quantity, source_json, searchable_text, dispatch_status
        ) VALUES ('batch-cars', 'sop|ship|lot01', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子',
          '2026-05-10', '2026-05-10', 'lot01', 10000, '{}', '马兰探险 汐子 铁矿', 'in_progress')
        """
    )
    agent.db.commit()
    rail_db = tmp_path / "rail_detail.sqlite3"
    conn = sqlite3.connect(rail_db)
    conn.execute(
        """
        CREATE TABLE shipment_release_batch_matches (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT,
          planned_weight REAL,
          marked_weight REAL,
          shipment_car_no TEXT,
          inspection_car_no TEXT,
          latest_event_time TEXT,
          loaded_at TEXT,
          inspection_file TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO shipment_release_batch_matches
          (id, release_batch_id, planned_weight, marked_weight, shipment_car_no, inspection_car_no, latest_event_time, loaded_at, inspection_file)
        VALUES ('m1', 'batch-cars', 70.0, 70.0, '300001', '300001', '2026-05-10 08:00:00', '2026-05-10 07:50:00', '/tmp/inspection.jpg')
        """
    )
    conn.commit()
    conn.close()

    render_dispatch_board(business_db_path=tmp_db, rail_db_path=rail_db, output_path=tmp_path / "board.html")

    html = (tmp_path / "board.html").read_text(encoding="utf-8")
    assert "已发运车辆明细" in html
    assert "300001" in html
    assert "2026-05-10 08:00:00" in html
    assert "file:///tmp/inspection.jpg" in html


def test_render_dispatch_board_handles_empty_database(tmp_db, tmp_path):
    output = tmp_path / "empty.html"

    result = render_dispatch_board(business_db_path=tmp_db, rail_db_path=None, output_path=output)

    html = output.read_text(encoding="utf-8")
    assert result["release_batch_count"] == 0
    assert "暂无 release_batch 数据" in html
    assert "当前发运中批次数" in html


def test_render_dispatch_board_opens_rail_database_read_only(tmp_db, tmp_path):
    agent = BusinessDataAgent()
    agent.db.execute(
        """
        INSERT INTO release_batches (
          id, batch_key, project, ship_name, cargo_name, destination_station,
          notice_date, batch_sequence, batch_quantity, source_json, searchable_text
        ) VALUES ('batch-1', 'project|ship|lot01', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '汐子',
          '2026-05-10', 'lot01', 8248, '{}', '马兰探险 汐子 铁矿')
        """
    )
    agent.db.commit()
    rail_db = tmp_path / "rail.sqlite3"
    _create_rail_db(rail_db)
    before = rail_db.stat().st_mtime_ns

    render_dispatch_board(business_db_path=tmp_db, rail_db_path=rail_db, output_path=tmp_path / "board.html")

    after = rail_db.stat().st_mtime_ns
    assert after == before
