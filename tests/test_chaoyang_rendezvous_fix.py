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
