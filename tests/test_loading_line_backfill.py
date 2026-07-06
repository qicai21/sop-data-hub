from __future__ import annotations

import sqlite3

from sop_hub.sop.loading_line_backfill import (
    apply_backfill,
    decide_backfill,
    parse_workgroup_segments,
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
