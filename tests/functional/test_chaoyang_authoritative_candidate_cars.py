from __future__ import annotations

import json
import sqlite3

from sop_hub.sop.workflow_task_executor import (
    _event_excel_batch_specs,
    _persist_authoritative_candidate_cars,
    _validated_manual_candidate_context,
)


def test_authoritative_95306_cars_overwrite_candidate_count_for_excel_specs(tmp_path):
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, source_image_path TEXT, ship_name TEXT,"
        " release_batch_id TEXT, car_numbers_json TEXT, candidate_status TEXT,"
        " wagon_count INTEGER, parsed_json TEXT, updated_at TEXT)"
    )
    notice_cars = [f"18{i:05d}" for i in range(48)]
    authoritative = notice_cars[:46]
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, source_image_path, ship_name, release_batch_id, car_numbers_json, "
        " candidate_status, wagon_count) "
        "VALUES ('cand1','/tmp/notice.jpg','马兰幸福','batch1',?,'matched',48)",
        (json.dumps(notice_cars),),
    )

    _persist_authoritative_candidate_cars(
        conn,
        candidate_id="cand1",
        car_numbers=authoritative,
        shipments=[
            {"car_no": car_no, "ydid": f"Y{i+1}"}
            for i, car_no in enumerate(authoritative)
        ],
        recover={
            "window_total_count": 46,
            "notice_only": notice_cars[46:],
            "missing_from_notice": [],
            "corrections": [],
        },
    )
    conn.commit()

    row = conn.execute(
        "SELECT car_numbers_json, wagon_count, parsed_json "
        "FROM inspection_ingestion_candidates WHERE id='cand1'"
    ).fetchone()
    conn.close()

    assert len(json.loads(row[0])) == 46
    assert row[1] == 46
    parsed = json.loads(row[2])
    assert parsed["authoritative_cars"]["notice_only"] == notice_cars[46:]
    assert parsed["authoritative_cars"]["shipments"][0] == {
        "car_no": authoritative[0], "ydid": "Y1"
    }

    specs, all_matched, ships = _event_excel_batch_specs("cand1", db)
    assert all_matched is True
    assert ships == ["马兰幸福"]
    assert specs == [("batch1", authoritative, [f"Y{i+1}" for i in range(46)])]


def test_manual_binding_returns_open_batch_context_for_ocr_candidate(tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE release_batches (id TEXT, project TEXT, ship_name TEXT, "
        "destination_station TEXT, cargo_name TEXT, dispatch_status TEXT)"
    )
    db.execute(
        "INSERT INTO release_batches VALUES ('lot3', 'chaoyang_steel', '马兰幸福', '朝阳西', '铁矿', 'enriched')"
    )
    candidate = {
        # A prior field-check may replace reason with pending_review's cause;
        # the durable audit anchor is payload_json._manual_assignment.
        "reason": "candidate_missing_ship_dest_project",
        "release_batch_id": "lot3",
        "payload_json": json.dumps({"_manual_assignment": {"release_batch_id": "lot3"}}),
    }

    assert _validated_manual_candidate_context(db, candidate) == {
        "id": "lot3",
        "project_id": "chaoyang_steel",
        "ship_name": "马兰幸福",
        "destination": "朝阳西",
        "cargo_name": "铁矿",
    }
