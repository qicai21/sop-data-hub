"""守门:上传前校验(堵整批/核车箱数)+ 上传结果台账查重(2026-06-17)。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop import factory_upload_ledger as L
from sop_hub.sop.upload_validation import validate_dispatch_event_upload


# ── 台账查重 ────────────────────────────────────────────────────────────

def test_ledger_dedup_idempotent_and_flags_duplicate(tmp_path):
    db = str(tmp_path / "ledger.db")
    # 箱A:1门户ID;箱B:重传出2个门户ID
    L.record_observations([{"box_no": "A", "portal_id": 100, "order_id": "O1"}], db_path=db)
    L.record_observations([{"box_no": "B", "portal_id": 200, "order_id": "O1"}], db_path=db)
    L.record_observations([{"box_no": "B", "portal_id": 201, "order_id": "O1"}], db_path=db)
    # 幂等:同箱同ID再记不增行
    r = L.record_observations([{"box_no": "A", "portal_id": 100, "order_id": "O1"}], db_path=db)
    assert r["inserted"] == 0
    dups = L.find_duplicates("O1", db_path=db)
    assert len(dups) == 1
    assert dups[0]["box_no"] == "B" and dups[0]["n_ids"] == 2


def test_ledger_prune_keeps_recent(tmp_path):
    db = str(tmp_path / "ledger.db")
    L.record_observations([{"box_no": "A", "portal_id": 1, "order_id": "O"}], db_path=db)
    # 刚记的不会被 60 天 prune 掉
    assert L.prune_expired(db_path=db, days=60) == 0
    assert len(L.find_duplicates(db_path=db)) == 0  # 只1条不算重复


# ── 上传前校验 ──────────────────────────────────────────────────────────

def _seed(db: str):
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE wagon_shipments(
        id TEXT, car_no TEXT, ydid TEXT, batch_id TEXT,
        source_message_id TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE wagon_container_shipments(
        id TEXT, car_no TEXT, box_no TEXT, ydid TEXT, batch_id TEXT)""")
    # 两趟:trip1 (2车/4箱), trip2 (1车/2箱)
    rows = [
        ("w1", "C1", "Y1", "B", "trip1_d1", "2026-06-13T01:00"),
        ("w2", "C2", "Y2", "B", "trip1_d1", "2026-06-13T01:00"),
        ("w3", "C3", "Y3", "B", "trip2_d2", "2026-06-14T01:00"),
    ]
    conn.executemany("INSERT INTO wagon_shipments VALUES(?,?,?,?,?,?)", rows)
    boxes = [("C1", "Y1"), ("C1", "Y1"), ("C2", "Y2"), ("C2", "Y2"),
             ("C3", "Y3"), ("C3", "Y3")]
    for i, (c, y) in enumerate(boxes):
        conn.execute("INSERT INTO wagon_container_shipments VALUES(?,?,?,?,?)",
                     (f"b{i}", c, f"BOX{i}", y, "B"))
    conn.commit()
    conn.close()


def test_validation_passes_single_event(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db)
    # trip1 = w1,w2(2车/4箱);95306 库不存在 → 用 rail_db_path 指向同库的空表会让箱核失败,
    # 所以本测试只验"单事件 + 列序号 + 车数",rail 缺失分支单独验。
    v = validate_dispatch_event_upload(["w1", "w2"], db_path=db,
                                       rail_db_path=str(tmp_path / "norail.db"))
    assert v.car_count == 2
    assert v.box_count_local == 4
    assert v.trip_label == "第一列"
    assert v.source_message_ids == ["trip1_d1"]
    # 95306 库不可达 → 保守拒(箱数没法核)
    assert v.ok is False
    assert any("95306" in e for e in v.errors)


def test_validation_blocks_cross_trip_batch(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db)
    v = validate_dispatch_event_upload(["w1", "w2", "w3"], db_path=db,
                                       rail_db_path=str(tmp_path / "norail.db"))
    assert v.ok is False
    assert any("整批" in e or "趟" in e for e in v.errors)
    assert len(v.source_message_ids) == 2


def test_validation_empty_rejected(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db)
    v = validate_dispatch_event_upload([], db_path=db)
    assert v.ok is False
