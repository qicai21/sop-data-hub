from __future__ import annotations

import sqlite3


def _make_release_batch_db(db_path, status: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY,
        dispatch_status TEXT NOT NULL,
        dispatch_status_note TEXT,
        dispatch_status_updated_at TEXT,
        updated_at TEXT
    )""")
    conn.execute(
        "INSERT INTO release_batches (id, dispatch_status) VALUES ('baoli_lot1', ?)",
        (status,),
    )
    conn.commit()
    conn.close()


def test_inspection_chain_wagon_ingest_advances_enriched_batch_to_loading(tmp_path):
    from sop_hub.sop.workflow_task_executor import _advance_loading_after_wagon_ingest

    db = tmp_path / "sop.db"
    _make_release_batch_db(db, "enriched")

    res = _advance_loading_after_wagon_ingest(
        "baoli_lot1",
        project_id="zhongtang_special_steel",
        db_path=db,
        actual_in_db_count=53,
    )

    assert res["action"] == "advanced"
    assert res["from_phase"] == "enriched"
    assert res["to_phase"] == "loading"

    conn = sqlite3.connect(str(db))
    status = conn.execute(
        "SELECT dispatch_status FROM release_batches WHERE id='baoli_lot1'"
    ).fetchone()[0]
    log = conn.execute(
        "SELECT from_phase, to_phase, triggered_by FROM lifecycle_transition_log"
    ).fetchone()
    conn.close()

    assert status == "loading"
    assert log == ("enriched", "loading", "zhongtang_special_steel.inspection_chain")


def test_inspection_chain_loading_advance_does_not_rewind_later_status(tmp_path):
    from sop_hub.sop.workflow_task_executor import _advance_loading_after_wagon_ingest

    db = tmp_path / "sop.db"
    _make_release_batch_db(db, "all_loaded")

    res = _advance_loading_after_wagon_ingest(
        "baoli_lot1",
        project_id="zhongtang_special_steel",
        db_path=db,
        actual_in_db_count=53,
    )

    assert res["action"] == "rejected"
    assert "illegal transition" in res["reason"]

    conn = sqlite3.connect(str(db))
    status = conn.execute(
        "SELECT dispatch_status FROM release_batches WHERE id='baoli_lot1'"
    ).fetchone()[0]
    log_count = conn.execute("SELECT COUNT(*) FROM lifecycle_transition_log").fetchone()[0]
    conn.close()

    assert status == "all_loaded"
    assert log_count == 0


def test_inspection_chain_loading_advance_skips_without_wagon_facts(tmp_path):
    from sop_hub.sop.workflow_task_executor import _advance_loading_after_wagon_ingest

    db = tmp_path / "sop.db"
    _make_release_batch_db(db, "enriched")

    res = _advance_loading_after_wagon_ingest(
        "baoli_lot1",
        project_id="zhongtang_special_steel",
        db_path=db,
        actual_in_db_count=0,
    )

    assert res == {"skipped": True, "reason": "no_wagon_facts_in_db"}

    conn = sqlite3.connect(str(db))
    status = conn.execute(
        "SELECT dispatch_status FROM release_batches WHERE id='baoli_lot1'"
    ).fetchone()[0]
    conn.close()

    assert status == "enriched"
