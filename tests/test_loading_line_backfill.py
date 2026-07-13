from __future__ import annotations

import sqlite3

from sop_hub.sop.loading_line_backfill import (
    apply_backfill,
    decide_backfill,
    parse_workgroup_segments,
    sync_recent_loading_lines,
)


def _setup_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE wagon_shipments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            dispatch_train_code TEXT,
            ship_name TEXT,
            destination_name TEXT,
            ticketed_at TEXT,
            loading_line TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE wagon_container_shipments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            dispatch_train_code TEXT,
            ship_name TEXT,
            destination_name TEXT,
            ticketed_at TEXT,
            ydid TEXT,
            loading_line TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE message_inbox (
            id INTEGER PRIMARY KEY,
            group_name TEXT,
            msg_type TEXT,
            received_datetime TEXT,
            text_content TEXT
        )
        """
    )
    return conn


def test_parse_workgroup_segments_handles_mixed_ships():
    segs = parse_workgroup_segments('煤一   四平铁“马兰希望”18节（序号1-18），“蓝鳍”19节（序号19-39）')
    assert {(s.ship_name, s.car_count, s.lane, s.project_id) for s in segs} == {
        ("马兰希望", 18, "煤一", "jilin_jingang_jinzhou"),
        ("蓝鳍", 19, "煤一", "jilin_jingang_jinzhou"),
    }


def test_parse_workgroup_segments_handles_count_before_ship():
    segs = parse_workgroup_segments("十四道，朝阳西铁中联发1节。瓢儿屯铁新星海66，14节。共15节")
    assert ("中联发", 1, "十四道", "chaoyang_steel") in {
        (s.ship_name, s.car_count, s.lane, s.project_id) for s in segs
    }


def test_decide_and_apply_backfill():
    conn = _setup_db()
    try:
        for i in range(1, 56):
            conn.execute(
                """
                INSERT INTO wagon_shipments
                (id, project_id, dispatch_train_code, ship_name, destination_name, ticketed_at, loading_line)
                VALUES (?, 'chaoyang_steel', 'cg2606281', '马兰幸福', '朝阳西', '2026-06-28 10:36:00', '')
                """,
                (f"w{i}",),
            )
        conn.execute(
            """
            INSERT INTO message_inbox
            (id, group_name, msg_type, received_datetime, text_content)
            VALUES (1, '铁晟业务工作群', 'text', '2026-06-28 09:58:29', '煤五朝阳西铁，马兰幸福装55节')
            """
        )
        decisions = decide_backfill(conn, project_ids=["chaoyang_steel"])
        assert len(decisions) == 1
        assert decisions[0].status == "matched"
        assert decisions[0].lane == "煤五"

        applied = apply_backfill(conn, decisions)
        assert applied == 55
        remain = conn.execute(
            "SELECT COUNT(*) FROM wagon_shipments WHERE dispatch_train_code='cg2606281' AND loading_line='煤五'"
        ).fetchone()[0]
        assert remain == 55
    finally:
        conn.close()


def test_container_ledger_uses_the_same_group_text_matcher():
    conn = _setup_db()
    try:
        for i in range(1, 48):
            conn.execute(
                """
                INSERT INTO wagon_container_shipments
                (id, project_id, dispatch_train_code, ship_name, destination_name, ticketed_at, ydid, loading_line)
                VALUES (?, 'jilin_jingang_jinzhou', 'jg2607091', '马兰希望', '四平',
                        '2026-07-09 15:10:00', ?, '')
                """,
                (f"c{i}", f"Y{i}"),
            )
        conn.execute(
            """
            INSERT INTO message_inbox(id, group_name, msg_type, received_datetime, text_content)
            VALUES (2, '铁晟业务工作群', 'text', '2026-07-09 15:05:01', '煤六 四平铁 马兰希望47节')
            """
        )
        decisions = decide_backfill(
            conn, project_ids=["jilin_jingang_jinzhou"], since="2026-07-01",
        )
        assert len(decisions) == 1
        assert decisions[0].status == "matched"
        assert decisions[0].group.source_table == "wagon_container_shipments"
        assert apply_backfill(conn, decisions) == 47
        assert conn.execute(
            "SELECT COUNT(*) FROM wagon_container_shipments WHERE loading_line='煤六'"
        ).fetchone()[0] == 47
    finally:
        conn.close()


def test_jiusan_missing_rows_use_mode_specific_confirmed_defaults():
    conn = _setup_db()
    try:
        conn.execute(
            """
            INSERT INTO wagon_shipments
            (id, project_id, dispatch_train_code, ship_name, destination_name, ticketed_at, loading_line)
            VALUES ('b1', 'jiusan', 'js2607011', '美国', '新台子', '2026-07-01 08:00:00', '')
            """
        )
        conn.execute(
            """
            INSERT INTO wagon_container_shipments
            (id, project_id, dispatch_train_code, ship_name, destination_name, ticketed_at, ydid, loading_line)
            VALUES ('c1', 'jiusan', 'js2607012', '美国', '新台子', '2026-07-01 09:00:00', 'C1', '')
            """
        )
        decisions = decide_backfill(conn, project_ids=["jiusan"], since="2026-07-01")
        assert {(d.group.source_table, d.status, d.lane) for d in decisions} == {
            ("wagon_shipments", "fallback", "七道"),
            ("wagon_container_shipments", "fallback", "八道"),
        }
        assert apply_backfill(conn, decisions) == 2
    finally:
        conn.close()


def test_recent_sync_is_idempotent_after_filling_lines(tmp_path):
    db = tmp_path / "sop_agent.db"
    source = _setup_db()
    target = sqlite3.connect(db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO wagon_shipments
        (id, project_id, dispatch_train_code, ship_name, destination_name, ticketed_at, loading_line)
        VALUES ('w-sync', 'chaoyang_steel', 'cg2607129', '马兰幸福', '朝阳西',
                '2026-07-12 10:00:00', '')
        """
    )
    conn.execute(
        """
        INSERT INTO message_inbox(id, group_name, msg_type, received_datetime, text_content)
        VALUES (8, '铁晟业务工作群', 'text', '2026-07-12 09:55:00', '煤五 朝阳西铁 马兰幸福1节')
        """
    )
    conn.commit()
    conn.close()

    first = sync_recent_loading_lines(db, since="2026-07-01")
    second = sync_recent_loading_lines(db, since="2026-07-01")
    assert first["applied_rows"] == 1
    assert second["applied_rows"] == 0
