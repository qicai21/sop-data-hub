from __future__ import annotations

import sqlite3

from sop_hub.sop.inspection_text_rendezvous import enrich_unique_bare_candidate


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE inspection_ingestion_candidates (
          id TEXT, message_id TEXT, project_id TEXT, ship_name TEXT,
          destination TEXT, candidate_status TEXT, wagon_count INTEGER,
          reason TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE message_inbox (
          message_id TEXT, inspection_candidate_id TEXT, group_name TEXT,
          classification_label TEXT, document_type TEXT, received_datetime TEXT
        );
        """
    )
    return conn


def _add(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    count: int = 51,
    group_name: str = "铁晟业务工作群",
    classification: str = "检装车通知单",
) -> None:
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates VALUES "
        "(?, ?, '', '', '', 'pending_review', ?, 'missing', "
        "'2026-07-24T22:08:40+08:00', '')",
        (candidate_id, f"wx_{candidate_id}", count),
    )
    conn.execute(
        "INSERT INTO message_inbox VALUES (?, ?, ?, ?, '', ?)",
        (
            f"wx_{candidate_id}",
            candidate_id,
            group_name,
            classification,
            "2026-07-24 22:08:40",
        ),
    )


def test_enriches_one_exact_trusted_bare_candidate():
    conn = _conn()
    _add(conn, "cand51")

    row = enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
        expected_count=51,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-24 20:00:00", "2026-07-25 03:00:00"),
    )

    assert row is not None
    assert row["id"] == "cand51"
    assert row["project_id"] == "zhongtang_special_steel"
    assert row["ship_name"] == "宝丽"
    assert row["destination"] == "汐子"
    assert row["candidate_status"] == "candidate"
    assert row["reason"] == "text_rendezvous_context_enriched"


def test_enriches_unique_candidate_that_only_lacks_destination():
    conn = _conn()
    _add(conn, "cand48", count=48)
    conn.execute(
        """
        UPDATE inspection_ingestion_candidates
        SET project_id='zhongtang_special_steel',
            ship_name='鞍子河',
            destination='',
            created_at='2026-07-26T09:07:28+08:00'
        WHERE id='cand48'
        """
    )
    conn.execute(
        "UPDATE message_inbox SET received_datetime='2026-07-25 23:00:32' "
        "WHERE inspection_candidate_id='cand48'"
    )

    row = enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="鞍子河",
        destination="汐子",
        expected_count=48,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-25 20:00:00", "2026-07-26 04:00:00"),
    )

    assert row is not None
    assert row["id"] == "cand48"
    assert row["project_id"] == "zhongtang_special_steel"
    assert row["ship_name"] == "鞍子河"
    assert row["destination"] == "汐子"
    assert row["candidate_status"] == "candidate"


def test_refuses_partially_enriched_candidate_with_conflicting_context():
    conn = _conn()
    _add(conn, "wrong_ship", count=48)
    conn.execute(
        """
        UPDATE inspection_ingestion_candidates
        SET project_id='zhongtang_special_steel',
            ship_name='宝丽',
            destination=''
        WHERE id='wrong_ship'
        """
    )

    assert enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="鞍子河",
        destination="汐子",
        expected_count=48,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-25 20:00:00", "2026-07-26 04:00:00"),
    ) is None


def test_refuses_non_exact_or_ambiguous_candidates():
    conn = _conn()
    _add(conn, "cand50", count=50)
    assert enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
        expected_count=51,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-24 20:00:00", "2026-07-25 03:00:00"),
    ) is None
    _add(conn, "cand51a")
    _add(conn, "cand51b")
    assert enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
        expected_count=51,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-24 20:00:00", "2026-07-25 03:00:00"),
    ) is None


def test_refuses_wrong_group_or_untrusted_document():
    conn = _conn()
    _add(conn, "wrong_group", group_name="别的群")
    _add(conn, "wrong_doc", classification="现场照片")
    assert enrich_unique_bare_candidate(
        conn,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
        expected_count=51,
        group_name="铁晟业务工作群",
        event_bounds=("2026-07-24 20:00:00", "2026-07-25 03:00:00"),
    ) is None
