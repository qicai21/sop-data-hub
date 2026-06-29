"""#144:95306 时间窗反推锚点选错(锚到同车旧票)回归测试。

车皮会反复发运:同 car_no + 同到站历史上有旧票。锚点查询不带时间下界时
ORDER BY ticketed_at DESC 会拿到旧批次的票,把整个时间窗锚错位。
修复:min_ticketed_at(= 通知时间 - 12h)以前的票视为"本批还没制票"。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sop_hub.sop.inspection_window_recover import recover_loading_cars_via_window


@pytest.fixture()
def rail_db(tmp_path):
    db = tmp_path / "rail.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE shipments ("
        " ydid TEXT PRIMARY KEY, car_no TEXT, destination_name TEXT,"
        " cargo_name TEXT, ticketed_at TEXT)"
    )
    rows = [
        # 旧批次:车 1000001/1000002 在 5/20 发过同到站同货
        ("old-1", "1000001", "朝阳西", "铁矿", "2026-05-20 10:00:00"),
        ("old-2", "1000002", "朝阳西", "铁矿", "2026-05-20 10:01:00"),
        # 本批次:6/11 19:00 窗口 3 车
        ("new-1", "1000001", "朝阳西", "铁矿", "2026-06-11 19:00:00"),
        ("new-2", "1000002", "朝阳西", "铁矿", "2026-06-11 19:02:00"),
        ("new-3", "1000003", "朝阳西", "铁矿", "2026-06-11 19:05:00"),
    ]
    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def test_anchor_skips_old_ticket_when_batch_not_ticketed(rail_db, tmp_path):
    """本批还没制票 + 锚点车有旧票 → 必须 no_ticket_yet,不能锚到旧票。"""
    conn = sqlite3.connect(str(rail_db))
    conn.execute("DELETE FROM shipments WHERE ydid LIKE 'new-%'")
    conn.commit()
    conn.close()

    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001", "1000002"],
        all_notice_car_nos=["1000001", "1000002", "1000003"],
        destination="朝阳西",
        min_ticketed_at="2026-06-11 06:00:00",
    )
    assert r["status"] == "no_ticket_yet"
    assert r["anchor_car_no"] is None


def test_anchor_picks_current_batch_ticket(rail_db):
    """新旧票都在,下界把旧票排除,窗内反推出本批 3 车。"""
    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001", "1000002"],
        all_notice_car_nos=["1000001", "1000002", "1000003"],
        destination="朝阳西",
        min_ticketed_at="2026-06-11 06:00:00",
    )
    assert r["status"] == "ok"
    assert r["anchor_ticketed_at"] == "2026-06-11 19:00:00"
    assert r["loading_car_nos"] == ["1000001", "1000002", "1000003"]


def test_legacy_no_bound_anchors_old_ticket(rail_db, tmp_path):
    """不带下界(老行为)会锚到旧票 — 文档化 #144 的 bug 形态。"""
    conn = sqlite3.connect(str(rail_db))
    conn.execute("DELETE FROM shipments WHERE ydid LIKE 'new-%'")
    conn.commit()
    conn.close()

    r = recover_loading_cars_via_window(
        rail_db_path=rail_db,
        loading_car_nos=["1000001"],
        all_notice_car_nos=["1000001", "1000002"],
        destination="朝阳西",
    )
    # 老行为:锚到 5/20 旧票,窗内交出旧批 2 车 — 这就是 #144 要避免的错位
    assert r["anchor_ticketed_at"] == "2026-05-20 10:00:00"


# ── 通知单错号 vs 95306 自动核对纠错(#检装车号95306自动核对纠错)──────────────

from sop_hub.sop.inspection_window_recover import (  # noqa: E402
    _autocorrect_notice_car_numbers,
    _similar_car_no,
    persist_car_no_corrections,
)


@pytest.fixture()
def autocorrect_rail_db(tmp_path):
    """丰收散运形态:窗内 5 真装车;通知单把其中 2 个录错(差 1 位)。"""
    db = tmp_path / "rail.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE shipments ("
        " ydid TEXT PRIMARY KEY, car_no TEXT, destination_name TEXT,"
        " cargo_name TEXT, ticketed_at TEXT)"
    )
    # 95306 窗内真号:1846879、1721344 + 3 个完全对得上的
    true_cars = ["1846879", "1721344", "2000001", "2000002", "2000003"]
    rows = [
        (f"y{i}", car, "朝阳西", "铁矿", f"2026-06-29 10:0{i}:00")
        for i, car in enumerate(true_cars)
    ]
    conn.executemany("INSERT INTO shipments VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def test_autocorrect_two_wrong_notice_numbers_unique_pairing(autocorrect_rail_db):
    """① 2 错号 ↔ 2 近似真号唯一配对 → 自动更正,状态 ok,不再挂 pending。"""
    notice = ["1848879", "1731344", "2000001", "2000002", "2000003"]
    r = recover_loading_cars_via_window(
        rail_db_path=autocorrect_rail_db,
        loading_car_nos=notice,
        all_notice_car_nos=notice,
        destination="朝阳西",
        min_ticketed_at="2026-06-29 00:00:00",
    )
    assert r["status"] == "ok"
    assert r["auto_corrected"] is True
    mapping = {c["notice_car_no"]: c["window_car_no"] for c in r["corrections"]}
    assert mapping == {"1848879": "1846879", "1731344": "1721344"}
    # 纠错后的真号并入真装车权威列表,异常/真排车清空
    assert set(r["loading_car_nos"]) == {
        "1846879", "1721344", "2000001", "2000002", "2000003"}
    assert r["missing_from_notice"] == []
    assert r["notice_only"] == []


def test_autocorrect_switch_off_keeps_anomaly(autocorrect_rail_db):
    """关停开关 → 维持 anomaly(原 pending_review 行为),不纠错。"""
    notice = ["1848879", "1731344", "2000001", "2000002", "2000003"]
    r = recover_loading_cars_via_window(
        rail_db_path=autocorrect_rail_db,
        loading_car_nos=notice,
        all_notice_car_nos=notice,
        destination="朝阳西",
        min_ticketed_at="2026-06-29 00:00:00",
        auto_correct_car_no=False,
    )
    assert r["status"] == "anomaly"
    assert r["corrections"] == []
    assert r["auto_corrected"] is False


def test_autocorrect_count_mismatch_keeps_anomaly(tmp_path):
    """② 数量不等(真排车数 != 异常数)→ 不纠错,维持 anomaly。"""
    db = tmp_path / "rail.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE shipments (ydid TEXT PRIMARY KEY, car_no TEXT,"
        " destination_name TEXT, cargo_name TEXT, ticketed_at TEXT)"
    )
    # 窗内 2 个不在通知单的(异常),但通知单只有 1 个对不上(真排车)
    true_cars = ["1846879", "9999999", "2000001", "2000002"]
    conn.executemany(
        "INSERT INTO shipments VALUES (?,?,?,?,?)",
        [(f"y{i}", c, "朝阳西", "铁矿", f"2026-06-29 10:0{i}:00")
         for i, c in enumerate(true_cars)],
    )
    conn.commit()
    conn.close()
    notice = ["1848879", "2000001", "2000002"]
    r = recover_loading_cars_via_window(
        rail_db_path=db, loading_car_nos=notice, all_notice_car_nos=notice,
        destination="朝阳西", min_ticketed_at="2026-06-29 00:00:00",
    )
    assert r["status"] == "anomaly"
    assert r["corrections"] == []


def test_autocorrect_ambiguous_pairing_aborts():
    """② 配对不唯一(一个错号在阈值内有 ≥2 个近似真号)→ 整体放弃。"""
    out = _autocorrect_notice_car_numbers(
        notice_only=["1846870"],
        window_only=["1846879", "1846878"],
        max_edit_distance=2,
        max_pairs=3,
    )
    assert out == []


def test_autocorrect_difference_too_large_aborts():
    """② 差异过大(超阈值)/不同长度 → 不算近似,不纠错。"""
    assert _autocorrect_notice_car_numbers(
        notice_only=["1111111"], window_only=["9999999"],
        max_edit_distance=2, max_pairs=3) == []
    assert _similar_car_no("184887", "1848879", 2)[0] is False  # 长度不同
    assert _similar_car_no("1721344", "1271344", 2)[2] == "transposition"  # 相邻转位


def test_autocorrect_exceeds_max_pairs_aborts():
    """② 配对对数超 max_pairs → 视为不可信,不纠错。"""
    out = _autocorrect_notice_car_numbers(
        notice_only=["1000001", "1000003", "1000005", "1000007"],
        window_only=["1000002", "1000004", "1000006", "1000008"],
        max_edit_distance=2,
        max_pairs=3,
    )
    assert out == []


def test_persist_corrections_writes_audit_and_updates_candidate(tmp_path):
    """③ 审计日志写入正确 + 候选 car_numbers_json 更正 + 幂等。"""
    db = tmp_path / "biz.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, car_numbers_json TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates VALUES (?,?,?)",
        ("cand1",
         json.dumps(["1848879", "1731344", "2000001"], ensure_ascii=False),
         "old"),
    )
    conn.commit()
    corrections = [
        {"notice_car_no": "1848879", "window_car_no": "1846879",
         "edit_distance": 1, "match_reason": "substitution",
         "source": "95306_window"},
        {"notice_car_no": "1731344", "window_car_no": "1721344",
         "edit_distance": 1, "match_reason": "substitution",
         "source": "95306_window"},
    ]
    written = persist_car_no_corrections(
        conn, candidate_id="cand1", release_batch_id="rb1",
        corrections=corrections, anchor_car_no="2000001",
        anchor_ticketed_at="2026-06-29 10:00:00", window_minutes=120,
    )
    conn.commit()
    assert written == 2

    audit = conn.execute(
        "SELECT notice_car_no, window_car_no, edit_distance, match_reason,"
        " source, release_batch_id FROM inspection_car_no_corrections"
        " ORDER BY notice_car_no"
    ).fetchall()
    assert len(audit) == 2
    assert audit[0] == ("1731344", "1721344", 1, "substitution",
                        "95306_window", "rb1")
    # 候选车号集合已被更正为真号
    cars = json.loads(conn.execute(
        "SELECT car_numbers_json FROM inspection_ingestion_candidates"
        " WHERE id='cand1'").fetchone()[0])
    assert cars == ["1846879", "1721344", "2000001"]

    # 幂等:重放(延迟验证器重试)不重复落审计
    again = persist_car_no_corrections(
        conn, candidate_id="cand1", release_batch_id="rb1",
        corrections=corrections)
    conn.commit()
    assert again == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM inspection_car_no_corrections").fetchone()[0] == 2
    conn.close()
