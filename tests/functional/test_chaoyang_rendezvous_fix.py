"""#issue-20260623:朝阳发车触发器 rendezvous 退化守门。

今早中联发发车触发器抓了 6 天前 matched 的 44 车旧候选(数量/车号全错),且 'matched'
候选被反复抓 → 重传 3 次。锁住三道闸常量,防再退化。
"""
from sop_hub.sop import workflow_task_executor as w


def test_matched_excluded_from_active_candidates():
    # 'matched'=已消费,必须在失效集里(老代码漏了它→被反复重抓重传)
    assert "matched" in w._INACTIVE_CANDIDATE_STATUSES
    assert "superseded" in w._INACTIVE_CANDIDATE_STATUSES


def test_count_and_age_guards_exist():
    assert w._INSPECTION_TEXT_TRIGGER_COUNT_TOL >= 0
    assert w._INSPECTION_TEXT_TRIGGER_CANDIDATE_MAX_AGE_H > 0


def test_send_idempotency_key_uses_car_set_not_only_batch_and_count():
    batch = "batch-1"
    first = w._event_send_biz_key(
        [(batch, ["1000001", "1000002", "1000003"], None)],
        wagon_count=3,
    )
    first_reordered = w._event_send_biz_key(
        [(batch, ["1000003", "1000001", "1000002"], None)],
        wagon_count=3,
    )
    second_same_count = w._event_send_biz_key(
        [(batch, ["2000001", "2000002", "2000003"], None)],
        wagon_count=3,
    )

    assert first == first_reordered
    assert first != second_same_count


def test_sent_same_car_set_event_detects_legacy_key_duplicate(tmp_path):
    import sqlite3

    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE wagon_shipments (
      batch_id TEXT, source_message_id TEXT, car_no TEXT
    );
    CREATE TABLE external_action_log (
      id INTEGER PRIMARY KEY,
      project_id TEXT,
      action_type TEXT,
      action_status TEXT,
      message_id TEXT,
      idempotency_key TEXT,
      executed_at TEXT
    );
    """)
    conn.executemany(
        "INSERT INTO wagon_shipments VALUES (?,?,?)",
        [
            ("batch-1", "wx_old", "1000001"),
            ("batch-1", "wx_old", "1000002"),
            ("batch-1", "wx_old", "1000003"),
            ("batch-1", "wx_other", "2000001"),
            ("batch-1", "wx_other", "2000002"),
            ("batch-1", "wx_other", "2000003"),
        ],
    )
    conn.execute(
        "INSERT INTO external_action_log VALUES (?,?,?,?,?,?,?)",
        (
            1,
            "zhongtang_special_steel",
            "send_shipping_excel_wechat",
            "executed",
            "wx_old",
            "zhongtang_special_steel:send_shipping_excel_wechat:batches:batch-1:3",
            "2026-07-04T19:20:10+08:00",
        ),
    )
    conn.commit()
    conn.close()

    dup = w._find_sent_same_car_set_event(
        db_path=db,
        project_id="zhongtang_special_steel",
        batch_specs=[("batch-1", ["1000003", "1000001", "1000002"], None)],
    )
    assert dup is not None
    assert dup["source_message_id"] == "wx_old"
    assert dup["external_action_log_id"] == 1

    not_dup = w._find_sent_same_car_set_event(
        db_path=db,
        project_id="zhongtang_special_steel",
        batch_specs=[("batch-1", ["2000001", "2000002", "2000003"], None)],
    )
    assert not_dup is None


def test_send_idempotency_prefers_ydid_when_available():
    batch = "batch-1"
    first = w._event_send_biz_key(
        [(batch, ["1000001", "1000002"], ["YD2", "YD1"])],
        wagon_count=2,
    )
    same_ydid_different_car = w._event_send_biz_key(
        [(batch, ["9999991", "9999992"], ["YD1", "YD2"])],
        wagon_count=2,
    )
    other_ydid = w._event_send_biz_key(
        [(batch, ["1000001", "1000002"], ["YD1", "YD3"])],
        wagon_count=2,
    )

    assert first == same_ydid_different_car
    assert first != other_ydid


def test_ansteel_upload_biz_key_uses_ydid_not_only_batch_and_count():
    first = w._ansteel_upload_biz_key(
        "batch-1",
        [{"ydid": "YD2", "car_no": "1000002"}, {"ydid": "YD1", "car_no": "1000001"}],
        ["1000001", "1000002"],
    )
    same_ydid_different_car = w._ansteel_upload_biz_key(
        "batch-1",
        [{"ydid": "YD1", "car_no": "2000001"}, {"ydid": "YD2", "car_no": "2000002"}],
        ["2000001", "2000002"],
    )
    other_ydid = w._ansteel_upload_biz_key(
        "batch-1",
        [{"ydid": "YD1", "car_no": "1000001"}, {"ydid": "YD3", "car_no": "1000003"}],
        ["1000001", "1000003"],
    )

    assert first == same_ydid_different_car
    assert first != other_ydid


def test_query_filters_exclude_stale(tmp_path):
    """复现今早:matched/车数对不上/陈年 候选都不该被选;只选 新鲜+车数对的。"""
    import sqlite3
    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE inspection_ingestion_candidates "
              "(id TEXT, ship_name TEXT, destination TEXT, candidate_status TEXT, "
              " wagon_count INT, created_at TEXT)")
    rows = [
        ("old_matched", "中联发", "朝阳西", "matched", 44, "2026-06-17 03:00:00"),   # 已消费+陈年+车数off
        ("cnt_off",     "中联发", "朝阳西", "candidate", 44, "datetime('now')"),       # 车数对不上
        ("good",        "中联发", "朝阳西", "candidate", 50, "datetime('now')"),       # 应中
    ]
    for r in rows[:2]:
        c.execute("INSERT INTO inspection_ingestion_candidates VALUES (?,?,?,?,?,?)", r)
    c.execute("INSERT INTO inspection_ingestion_candidates VALUES (?,?,?,?,?,datetime('now'))", rows[2][:5])
    c.commit()
    expected, tol = 50, w._INSPECTION_TEXT_TRIGGER_COUNT_TOL
    ph = ",".join("?" * len(w._INACTIVE_CANDIDATE_STATUSES))
    q = (f"SELECT id FROM inspection_ingestion_candidates WHERE ship_name='中联发' "
         f"AND candidate_status NOT IN ({ph}) "
         f"AND created_at >= datetime('now','-24 hours') "
         f"AND (?=0 OR ABS(COALESCE(wagon_count,0)-?)<=?) ORDER BY created_at DESC LIMIT 1")
    got = c.execute(q, [*w._INACTIVE_CANDIDATE_STATUSES, expected, expected, tol]).fetchone()
    assert got is not None and got[0] == "good"


def test_already_handled_matched_uses_trigger_event_window(tmp_path):
    import sqlite3
    from pathlib import Path

    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT, message_id TEXT, ship_name TEXT, destination TEXT,
      candidate_status TEXT, wagon_count INT, created_at TEXT
    );
    CREATE TABLE workflow_task_db (id INTEGER, created_at TEXT);
    CREATE TABLE message_inbox (id INTEGER, received_datetime TEXT);
    """)
    c.execute(
        "INSERT INTO inspection_ingestion_candidates VALUES (?,?,?,?,?,?,?)",
        ("old55", "wx_old", "马兰幸福", "朝阳西", "matched", 55, "2026-06-28 06:50:00"),
    )
    c.execute("INSERT INTO workflow_task_db VALUES (1, datetime('now'))")
    c.execute("INSERT INTO message_inbox VALUES (100, '2026-06-30 06:51:00')")
    c.commit()
    c.close()

    res = w._execute_inspection_text_trigger(
        {
            "trigger_project": "chaoyang_steel",
            "trigger_ship": "马兰幸福",
            "trigger_dest": "朝阳西",
            "trigger_expected_count": 53,
            "message_inbox_id": 100,
            "received_datetime": "2026-06-30 06:51:00",
        },
        "wx_text",
        db_path=Path(db),
        task_id=1,
    )

    assert (res.get("output_json") or {}).get("stage") != "already_handled_by_notice_chain"
    assert res["status"] in {"pending", "skipped", "failed"}


def test_already_handled_requires_exact_wagon_count(tmp_path):
    import sqlite3
    from pathlib import Path

    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
    CREATE TABLE inspection_ingestion_candidates (
      id TEXT, message_id TEXT, ship_name TEXT, destination TEXT,
      candidate_status TEXT, wagon_count INT, created_at TEXT
    );
    CREATE TABLE workflow_task_db (id INTEGER, created_at TEXT);
    CREATE TABLE message_inbox (id INTEGER, received_datetime TEXT);
    """)
    c.execute(
        "INSERT INTO inspection_ingestion_candidates VALUES (?,?,?,?,?,?,?)",
        ("old50", "wx_old", "宝丽", "汐子", "matched", 50, "2026-07-24 01:11:00"),
    )
    c.execute("INSERT INTO workflow_task_db VALUES (1, datetime('now'))")
    c.execute("INSERT INTO message_inbox VALUES (100, '2026-07-24 21:55:23')")
    c.commit()
    c.close()

    res = w._execute_inspection_text_trigger(
        {
            "trigger_project": "zhongtang_special_steel",
            "trigger_ship": "宝丽",
            "trigger_dest": "汐子",
            "trigger_expected_count": 51,
            "message_inbox_id": 100,
            "received_datetime": "2026-07-24 21:55:23",
        },
        "wx_text",
        db_path=Path(db),
        task_id=1,
    )

    assert (res.get("output_json") or {}).get("stage") != "already_handled_by_notice_chain"
