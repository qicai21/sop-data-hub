import sqlite3

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
