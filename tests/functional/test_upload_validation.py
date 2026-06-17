"""守门:上传前校验 —— 堵整批 + 核车箱数 + 必备字段挂起(2026-06-17)。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop.upload_validation import validate_dispatch_event_upload


def _seed(db: str, *, hph_ok: bool = True):
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE wagon_shipments(
        id TEXT, car_no TEXT, ydid TEXT, hph TEXT, marked_weight REAL,
        batch_id TEXT, source_message_id TEXT, created_at TEXT, ticketed_at TEXT)""")
    conn.execute("""CREATE TABLE wagon_container_shipments(
        id TEXT, car_no TEXT, box_no TEXT, ydid TEXT, batch_id TEXT)""")
    # trip1(2车/4箱) + trip2(1车/2箱)
    h = (lambda i: f"GZ{i}") if hph_ok else (lambda i: "")
    rows = [
        ("w1", "C1", "Y1", h(1), 64.0, "B", "t1", "c", "2026-06-13T01:00"),
        ("w2", "C2", "Y2", h(2), 64.0, "B", "t1", "c", "2026-06-13T01:00"),
        ("w3", "C3", "Y3", h(3), 64.0, "B", "t2", "c", "2026-06-14T01:00"),
    ]
    conn.executemany("INSERT INTO wagon_shipments VALUES(?,?,?,?,?,?,?,?,?)", rows)
    boxes = [("C1", "Y1"), ("C1", "Y1"), ("C2", "Y2"), ("C2", "Y2"),
             ("C3", "Y3"), ("C3", "Y3")]
    for i, (c, y) in enumerate(boxes):
        conn.execute("INSERT INTO wagon_container_shipments VALUES(?,?,?,?,?)",
                     (f"b{i}", c, f"BOX{i}", y, "B"))
    conn.commit()
    conn.close()


def test_single_event_fields_and_trip(tmp_path):
    """完整单事件:必备字段齐 + 列序号定位 + 车箱数。"""
    db = str(tmp_path / "sop.db")
    _seed(db)
    v = validate_dispatch_event_upload(["w1", "w2"], db_path=db,
                                       rail_db_path=str(tmp_path / "norail.db"))
    assert v.car_count == 2
    assert v.box_count_local == 4
    assert v.trip_label == "第一列"
    # 字段齐 → 不报字段错(只剩 95306 不可达那条)
    assert not any("必备字段" in e for e in v.errors)


def test_blocks_incomplete_fields_suspend(tmp_path):
    """缺货票号 → 挂起,报必备字段不全。"""
    db = str(tmp_path / "sop.db")
    _seed(db, hph_ok=False)
    v = validate_dispatch_event_upload(["w1", "w2"], db_path=db,
                                       rail_db_path=str(tmp_path / "norail.db"))
    assert v.ok is False
    assert any("必备字段" in e and "挂起" in e for e in v.errors)


def test_blocks_cross_trip_batch(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db)
    v = validate_dispatch_event_upload(["w1", "w2", "w3"], db_path=db,
                                       rail_db_path=str(tmp_path / "norail.db"))
    assert v.ok is False
    assert any("整批" in e or "趟" in e for e in v.errors)
    assert len(v.source_message_ids) == 2


def test_empty_rejected(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db)
    v = validate_dispatch_event_upload([], db_path=db)
    assert v.ok is False
