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
          source_file_name, source_json, searchable_text, dispatch_status
        ) VALUES (
          'batch-1', 'project|ship|lot01', '中唐特钢铁矿发运项目', '马兰探险', '铁矿', '铁矿粉',
          '汐子', '2026-05-10', '2026-05-10', 'lot01', 8248,
          '/tmp/malan.jpg', '{"image_path":"/tmp/malan.jpg","json_path":"/tmp/malan_result.json","status_path":"/tmp/malan_status.json"}',
          '马兰探险 汐子 铁矿', 'in_progress'
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
