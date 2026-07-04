from __future__ import annotations

import sqlite3

import sop_hub.sop.pending_match_verifier as pmv


def _db_with_pending_candidate(tmp_path, *, created_at: str) -> str:
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, group_name TEXT, source_file_name TEXT, ship_name TEXT,"
        " candidate_status TEXT, release_batch_id TEXT, created_at TEXT, updated_at TEXT,"
        " reason TEXT)"
    )
    conn.execute(
        "INSERT INTO inspection_ingestion_candidates "
        "(id, group_name, source_file_name, ship_name, candidate_status, "
        " release_batch_id, created_at, updated_at) "
        "VALUES ('cand1','铁晟业务工作群','x.jpg','马兰幸福',"
        " 'pending_95306_match','batch1',?,?)",
        (created_at, created_at),
    )
    conn.commit()
    conn.close()
    return str(db)


def test_verifier_retries_before_timeout_and_can_succeed(tmp_path, monkeypatch):
    db = _db_with_pending_candidate(tmp_path, created_at="2026-01-01T00:00:00+08:00")

    monkeypatch.setattr(
        pmv,
        "_retry_chain",
        lambda cand_id, db_path: {
            "status": "succeeded",
            "output_json": {
                "send": {"success": True},
                "consignee_upload": {"success": True, "skipped": False},
            },
        },
    )
    monkeypatch.setattr(pmv, "_send_timeout_notice", lambda *a, **k: None)

    summary = pmv.verify_pending_candidates(
        db_path=db,
        timeout_hours=0.01,
        send_timeout_notice=True,
    )

    assert summary.retried == 1
    assert summary.succeeded == 1
    assert summary.timed_out == 0


def test_verifier_times_out_only_after_retry_still_waiting(tmp_path, monkeypatch):
    db = _db_with_pending_candidate(tmp_path, created_at="2026-01-01T00:00:00+08:00")
    notices: list[tuple] = []

    monkeypatch.setattr(
        pmv,
        "_retry_chain",
        lambda cand_id, db_path: {
            "status": "skipped",
            "output_json": {
                "stage": "waiting_95306_tickets",
                "actual_in_db_count": 46,
                "expected_count": 48,
            },
        },
    )
    monkeypatch.setattr(pmv, "_send_timeout_notice", lambda *a, **k: notices.append(a))

    summary = pmv.verify_pending_candidates(
        db_path=db,
        timeout_hours=0.01,
        send_timeout_notice=True,
    )

    assert summary.retried == 1
    assert summary.succeeded == 0
    assert summary.timed_out == 1
    assert notices

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT candidate_status, reason FROM inspection_ingestion_candidates WHERE id='cand1'"
    ).fetchone()
    conn.close()
    assert row[0] == "timeout_manual_review"
    assert row[1].startswith("95306_ticket_timeout:")
