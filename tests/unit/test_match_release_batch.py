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


def test_explicit_priority_resolves_multiple_open_lots():
    conn = _conn()
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("lot03", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉", "enriched", "2026-07-17", "2026-07-17"),
            ("lot04", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉", "enriched", "2026-07-18", "2026-07-18"),
        ],
    )
    conn.execute(
        "CREATE TABLE release_dispatch_match_rules "
        "(release_batch_id TEXT, status TEXT, priority INTEGER)"
    )
    conn.executemany(
        "INSERT INTO release_dispatch_match_rules VALUES (?,?,?)",
        [("lot03", "active", 10), ("lot04", "active", 20)],
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="zhongtang_special_steel", ship_name="宝丽",
        destination_station="汐子", cargo_name="铁矿", db_conn=conn,
    )

    assert result.reason == "explicit_priority"
    assert result.matched_release_batch_id == "lot03"


def test_all_loaded_batch_never_matches():
    """终态 all_loaded 不在 open 集，即使是唯一批次也不接新车。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        ("lot5", "jilin_jingang_jinzhou", "马兰希望", "四平", "铁矿", "金布巴粉",
         "all_loaded", "2026-07-08", "2026-07-08"),
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="jilin_jingang_jinzhou", ship_name="马兰希望",
        destination_station="四平", cargo_name="铁矿", db_conn=conn,
    )

    assert result.reason == "no_open_batch"
    assert result.matched_release_batch_id is None


def test_completed_dispatch_rule_does_not_override_open_lot():
    """match rule completed 的 lot 不参与 priority；只剩另一 open lot 时 single_match。"""
    conn = _conn()
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("lot05", "jilin_jingang_jinzhou", "马兰希望", "四平", "铁矿", "金布巴粉",
             "all_loaded", "2026-07-08", "2026-07-08"),
            ("lot06", "jilin_jingang_jinzhou", "马兰希望", "四平", "铁矿", "金布巴粉",
             "loading", "2026-07-16", "2026-07-16"),
        ],
    )
    conn.execute(
        "CREATE TABLE release_dispatch_match_rules "
        "(release_batch_id TEXT, status TEXT, priority INTEGER)"
    )
    conn.executemany(
        "INSERT INTO release_dispatch_match_rules VALUES (?,?,?)",
        [("lot05", "completed", 10), ("lot06", "active", 20)],
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="jilin_jingang_jinzhou", ship_name="马兰希望",
        destination_station="四平", cargo_name="铁矿", db_conn=conn,
    )

    assert result.reason == "single_match"
    assert result.matched_release_batch_id == "lot06"


def test_full_dispatch_plan_excludes_lot_from_open_candidates():
    """allocated>=planned 的分票计划 lot 不再作为 open 候选。"""
    conn = _conn()
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("lot05", "jilin_jingang_jinzhou", "马兰希望", "四平", "铁矿", "金布巴粉",
             "loading", "2026-07-08", "2026-07-08"),
            ("lot06", "jilin_jingang_jinzhou", "马兰希望", "四平", "铁矿", "金布巴粉",
             "enriched", "2026-07-16", "2026-07-16"),
        ],
    )
    conn.execute(
        "CREATE TABLE release_batch_dispatch_plan ("
        "release_batch_id TEXT, planned_box_count INT, allocated_box_count INT, status TEXT)"
    )
    conn.execute(
        "INSERT INTO release_batch_dispatch_plan VALUES (?,?,?,?)",
        ("lot05", 308, 308, "active"),
    )

    result = match_release_batch_by_ship_destination_cargo(
        project_id="jilin_jingang_jinzhou", ship_name="马兰希望",
        destination_station="四平", cargo_name="铁矿", db_conn=conn,
    )

    assert result.reason == "single_match"
    assert result.matched_release_batch_id == "lot06"


def test_entry_matrix_priority_match_and_infer_agree(tmp_path):
    """入口矩阵：match 与 infer 同分路径对同一 fixture 结论一致。"""
    from sop_hub.sop.infer_candidate_context import infer_candidate_context

    db_path = tmp_path / "matrix.db"
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
            ("lot03", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉",
             "enriched", "2026-07-17", "2026-07-17"),
            ("lot04", "zhongtang_special_steel", "宝丽", "汐子", "铁矿", "麦克粉",
             "enriched", "2026-07-18", "2026-07-18"),
        ],
    )
    conn.executemany(
        "INSERT INTO release_dispatch_match_rules VALUES (?,?,?)",
        [("lot03", "active", 10), ("lot04", "active", 20)],
    )
    conn.commit()

    match = match_release_batch_by_ship_destination_cargo(
        project_id="zhongtang_special_steel", ship_name="宝丽",
        destination_station="汐子", cargo_name="铁矿", db_conn=conn,
    )
    infer = infer_candidate_context(
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
    conn.close()

    assert match.reason == "explicit_priority"
    assert match.matched_release_batch_id == "lot03"
    assert infer.matched is True
    assert infer.reason == "explicit_priority"
    assert infer.release_batch_id == match.matched_release_batch_id
