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


def _db_with_mixed_pending(tmp_path) -> str:
    """Mix: retryable pending_review + non-retryable OCR junk + classic pending_95306."""
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates ("
        " id TEXT PRIMARY KEY, group_name TEXT, source_file_name TEXT, ship_name TEXT,"
        " candidate_status TEXT, release_batch_id TEXT, created_at TEXT, updated_at TEXT,"
        " reason TEXT)"
    )
    now = "2026-07-19T10:00:00+08:00"
    rows = [
        ("c-multi", "pending_review", "multiple_candidates"),
        ("c-tied", "pending_review", "multi_candidate_tied:2"),
        ("c-no-open", "pending_review", "no_open_batch"),
        ("c-ocr", "pending_review", "ocr_ambiguous_footer"),  # not self-heal
        ("c-95306", "pending_95306_match", None),
        ("c-freight", "pending_freight_info", None),
    ]
    for cid, status, reason in rows:
        conn.execute(
            "INSERT INTO inspection_ingestion_candidates "
            "(id, group_name, source_file_name, ship_name, candidate_status, "
            " release_batch_id, created_at, updated_at, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, "g", "x.jpg", "船", status, "b1", now, now, reason),
        )
    conn.commit()
    conn.close()
    return str(db)


def test_is_retryable_pending_review_reasons():
    assert pmv._is_retryable_pending_review("multiple_candidates")
    assert pmv._is_retryable_pending_review("no_open_batch")
    assert pmv._is_retryable_pending_review("no_match")
    assert pmv._is_retryable_pending_review("bad_input")
    assert pmv._is_retryable_pending_review("multi_candidate_tied:2")
    assert not pmv._is_retryable_pending_review("ocr_ambiguous_footer")
    assert not pmv._is_retryable_pending_review(None)
    assert not pmv._is_retryable_pending_review("")


def test_verifier_retries_self_healable_pending_review_only(tmp_path, monkeypatch):
    db = _db_with_mixed_pending(tmp_path)
    seen: list[str] = []

    def _fake_retry(cand_id, db_path):
        seen.append(cand_id)
        return {
            "status": "succeeded",
            "output_json": {"send": {"success": True}},
        }

    monkeypatch.setattr(pmv, "_retry_chain", _fake_retry)
    monkeypatch.setattr(pmv, "_send_timeout_notice", lambda *a, **k: None)

    summary = pmv.verify_pending_candidates(
        db_path=db,
        timeout_hours=24.0,
        send_timeout_notice=False,
    )

    # 3 retryable pending_review + pending_95306 + pending_freight = 5
    # ocr_ambiguous_footer pending_review must be skipped
    assert set(seen) == {"c-multi", "c-tied", "c-no-open", "c-95306", "c-freight"}
    assert "c-ocr" not in seen
    assert summary.scanned == 5
    assert summary.retried == 5
    assert summary.succeeded == 5
