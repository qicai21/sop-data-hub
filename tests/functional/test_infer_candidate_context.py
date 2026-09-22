import sqlite3
from unittest.mock import patch

import sop_hub.sop.infer_candidate_context as candidate_context
from sop_hub.sop.infer_candidate_context import infer_candidate_context


def test_same_flow_evidence_tie_uses_explicit_priority(tmp_path):
    db_path = tmp_path / "sop.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, project TEXT, ship_name TEXT,
            destination_station TEXT, cargo_name TEXT, cargo_product_name TEXT,
            dispatch_status TEXT, notice_date TEXT, batch_date TEXT
        )"""
    )
    conn.execute(
        "CREATE TABLE release_dispatch_match_rules "
        "(release_batch_id TEXT, status TEXT, priority INTEGER)"
    )
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("lot03", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉", "enriched", "2026-07-17", "2026-07-17"),
            ("lot04", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉", "enriched", "2026-07-18", "2026-07-18"),
        ],
    )
    conn.executemany(
        "INSERT INTO release_dispatch_match_rules VALUES (?,?,?)",
        [("lot03", "active", 10), ("lot04", "active", 20)],
    )
    conn.commit()
    conn.close()

    result = infer_candidate_context(
        {
            "rows": [
                {"car_no": "4897159", "cargo_info_raw": "汐子铁矿"},
                {"car_no": "1624439", "cargo_info_raw": "宝丽"},
            ],
            "meta": {"daoxian": "煤四"},
        },
        group_name="",
        received_datetime="",
        db_path=db_path,
    )

    assert result.matched is True
    assert result.reason == "explicit_priority"
    assert result.release_batch_id == "lot03"


def test_text_fusion_matches_enriched_lot_and_preserves_full_context(tmp_path):
    """Regression: freight-enriched lots remain eligible for inspection text."""
    db_path = tmp_path / "sop.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, project TEXT, ship_name TEXT,
            destination_station TEXT, cargo_name TEXT, cargo_product_name TEXT,
            dispatch_status TEXT, notice_date TEXT, batch_date TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        ("zhonglianfa-lot02", "zhongtang_special_steel", "中联发", "汐子", "铁矿粉",
         "PB粉", "enriched", "2026-09-20", "2026-09-20"),
    )
    conn.commit()
    conn.close()

    with patch.object(candidate_context, "_try_text_fusion", return_value={
        "ship_name": "中联发", "project_id": "zhongtang_special_steel",
        "source_message_id": "wx_source_31",
    }):
        result = infer_candidate_context(
            {"rows": []}, group_name="铁晟业务工作群",
            received_datetime="2026-09-21 22:00:00", db_path=db_path,
        )

    assert result.matched is True
    assert result.source == "text_fusion"
    assert result.release_batch_id == "zhonglianfa-lot02"
    assert result.destination == "汐子"
    assert result.cargo_name == "铁矿粉"
