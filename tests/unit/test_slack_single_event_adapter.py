import json
import sqlite3
from datetime import datetime, timedelta, timezone

from sop_hub.sop.slack_single_event_adapter import _group013_attachment, _rail_shipments, execute_event


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def test_rail_lookup_matches_executor_newest_ticket_without_inspection_date_filter(tmp_path):
    db = tmp_path / "rail.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE shipments(ydid TEXT,car_no TEXT,destination_name TEXT,cargo_name TEXT,ticketed_at TEXT,status_name TEXT,latest_stage_key TEXT)")
    conn.executemany(
        "INSERT INTO shipments VALUES(?,?,?,?,?,?,?)",
        [
            ("old", "1000001", "汐子", "铁矿石", "2026-09-20 10:00:00", "制票", "ticketed"),
            ("new", "1000001", "汐子", "铁矿石", "2026-09-22 10:00:00", "发运", "departed"),
            ("wrong-dest", "1000002", "朝阳西", "铁矿石", "2026-09-22 10:00:00", "制票", "ticketed"),
        ],
    )
    conn.commit(); conn.close()
    rows, unresolved = _rail_shipments(["1000001", "1000002"], "汐子", db)
    assert [row["ydid"] for row in rows] == ["new"]
    assert unresolved == ["1000002"]


def test_expired_token_is_rejected_before_database_access(tmp_path):
    now = datetime.now(timezone.utc)
    result = execute_event(
        {"issued_at": iso(now - timedelta(hours=2)), "expires_at": iso(now - timedelta(hours=1)), "digest": "x", "facts": {}},
        db_path=tmp_path / "must-not-be-opened.db",
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "token_expired_or_not_yet_valid"
    assert not (tmp_path / "must-not-be-opened.db").exists()


def test_group013_requires_real_file_record(tmp_path):
    record = tmp_path / "2026-09.jsonl"
    record.write_text(
        json.dumps({"msg-type": "text", "msg-path": "/tmp/a.xlsx", "time": "2026-09-22 10:00:00"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"msg-type": "file", "msg-path": "/tmp/a.xlsx", "time": "2026-09-22 10:01:00"}, ensure_ascii=False)
        + "\n", encoding="utf-8",
    )
    assert _group013_attachment("a.xlsx", "2026-09-22 10:00:30", tmp_path)["time"] == "2026-09-22 10:01:00"
    assert _group013_attachment("a.xlsx", "2026-09-22 10:02:00", tmp_path) is None
