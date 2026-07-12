import sqlite3

from sop_hub.sop.match_release_batch import match_release_batch_by_ship_destination_cargo


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE release_batches (
            id TEXT PRIMARY KEY, project TEXT, ship_name TEXT,
            destination_station TEXT, cargo_name TEXT, cargo_product_name TEXT,
            dispatch_status TEXT, notice_date TEXT, batch_date TEXT
        )"""
    )
    return conn


def test_single_open_batch_matches_directly():
    conn = _conn()
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        ("lot1", "zhongtang_special_steel", "宝丽", "汐子", "铁矿粉", "麦克粉", "loading", "2026-07-03", "2026-07-03"),
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="zhongtang_special_steel", ship_name="宝丽",
        destination_station="汐子", cargo_name="铁矿粉", db_conn=conn,
    )

    assert result.reason == "single_match"
    assert result.matched_release_batch_id == "lot1"


def test_same_ship_multiple_lots_never_prefers_old_loading_lot():
    conn = _conn()
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("lot1", "zhongtang_special_steel", "宝丽", "汐子", "铁矿粉", "麦克粉", "loading", "2026-07-03", "2026-07-03"),
            ("lot2", "zhongtang_special_steel", "宝丽", "汐子", "铁矿粉", "麦克粉", "enriched", "2026-07-10", "2026-07-10"),
        ],
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="zhongtang_special_steel", ship_name="宝丽",
        destination_station="汐子", cargo_name="铁矿粉", db_conn=conn,
    )

    assert result.reason == "multiple_candidates"
    assert result.matched_release_batch_id is None
    assert result.candidate_release_batch_ids == ["lot2", "lot1"]
