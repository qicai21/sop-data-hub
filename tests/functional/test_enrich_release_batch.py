"""Functional tests for enrich_release_batch executor — R41."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone

import pytest

from sop_hub.sop.enrich_release_batch import (
    ReleaseBatchEnrichmentResult,
    enrich_release_batch_with_freight_detail,
)
from sop_hub.sop.freight_detail_extractor import (
    FreightDetailCandidate,
    extract_freight_detail,
)

# ── Test helpers ────────────────────────────────────────────────────────

_SAMPLE_TEXT = (
    "锦州港今日放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954"
)

_BATCH_ID = "rb_test_001"


def _make_complete_candidate() -> FreightDetailCandidate:
    """Return a complete FreightDetailCandidate with all fields populated."""
    return extract_freight_detail(_SAMPLE_TEXT)


def _create_test_db(db_path: str | Path, *, include_new_columns: bool = True) -> sqlite3.Connection:
    """Create a test release_batches table and insert a row.

    Args:
        db_path: path to the DB file (or ':memory:').
        include_new_columns: if False, omit R41 columns for schema_missing_fields test.

    Returns:
        sqlite3.Connection to the test DB.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    cols = [
        "id TEXT PRIMARY KEY",
        "batch_key TEXT NOT NULL UNIQUE",
        "project TEXT",
        "contract_id TEXT",
        "contract_no TEXT",
        "ship_name TEXT NOT NULL",
        "cargo_name TEXT NOT NULL",
        "cargo_product_name TEXT",
        "consignor TEXT",
        "consignee TEXT",
        "commissioner_identifier TEXT",
        "commissioner_note TEXT",
        "trade_type TEXT",
        "transport_mode TEXT",
        "destination_station TEXT",
        "yard_location TEXT",
        "customs_release_qty REAL",
        "notice_date TEXT NOT NULL",
        "batch_date TEXT",
        "batch_sequence TEXT",
        "batch_quantity REAL",
        "total_planned_quantity REAL",
        "remaining_quantity REAL",
        "batch_count INTEGER NOT NULL DEFAULT 0",
        "origin_station TEXT",
        "agent_name TEXT",
        "customer_name TEXT",
        "id_label TEXT",
        "actual_wagon_count INTEGER DEFAULT 0",
        "dispatch_status TEXT NOT NULL DEFAULT 'in_progress'",
        "dispatch_status_note TEXT",
        "dispatch_status_updated_at TEXT",
        "source_file_name TEXT",
        "source_json TEXT NOT NULL",
        "searchable_text TEXT NOT NULL",
        "plan_id TEXT",
        "order_id TEXT",
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    ]

    if include_new_columns:
        cols.extend([
            "order_identifier TEXT",
            "cargo_name_detail TEXT",
            "quantity_tons REAL",
            "source_message_id TEXT",
            "source_group_id TEXT",
        ])

    conn.execute(f"CREATE TABLE IF NOT EXISTS release_batches ({', '.join(cols)})")
    conn.execute(
        """INSERT INTO release_batches (id, batch_key, project, ship_name, cargo_name,
           destination_station, notice_date, contract_no, dispatch_status, source_json,
           searchable_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _BATCH_ID,
            "印粉|锦州港|2026-05-29",
            "jilin_jingang_jinzhou",
            "蓝鳍",
            "印粉",
            "四平",
            "2026-05-29",
            "",   # contract_no — intentionally empty
            "in_progress",
            "{}",
            "印粉 锦州港 蓝鳍",
        ),
    )
    conn.commit()
    return conn


# ── Test 1: FreightDetailCandidate complete → planned_updates ──────────

def test_complete_candidate_generates_planned_updates(tmp_path: Path):
    """FreightDetailCandidate with status=complete should generate planned_updates."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()
    assert candidate.status == "complete"

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=True, db_path=db_path,
    )

    assert result.status == "dry_run"
    assert result.planned_updates["order_identifier"] == "CGR20260520095954"
    assert result.planned_updates["contract_no"] == "HNMC20260520-1X-1"
    assert result.planned_updates["cargo_name_detail"] == "印粉"
    assert result.planned_updates["quantity_tons"] == 3000
    assert result.schema_missing_fields == []


# ── Test 2: dry-run does NOT write ─────────────────────────────────────

def test_dry_run_does_not_write(tmp_path: Path):
    """Dry-run should produce planned_updates but NOT modify the release_batches row."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=True, db_path=db_path,
    )
    assert result.status == "dry_run"
    assert result.planned_updates

    # Verify DB was not modified
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row is not None
    assert row["order_identifier"] is None  # not written
    assert row["contract_no"] == ""  # still empty string
    conn.close()


# ── Test 3: apply writes order_identifier ──────────────────────────────

def test_apply_writes_order_identifier(tmp_path: Path):
    """Apply should write order_identifier to the release_batches row."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    assert result.status == "applied"
    assert result.applied_fields["order_identifier"] == "CGR20260520095954"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["order_identifier"] == "CGR20260520095954"
    conn.close()


# ── Test 4: apply writes contract_no ───────────────────────────────────

def test_apply_writes_contract_no(tmp_path: Path):
    """Apply should write contract_no to the release_batches row."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["contract_no"] == "HNMC20260520-1X-1"
    conn.close()


# ── Test 5: apply writes cargo_name_detail ─────────────────────────────

def test_apply_writes_cargo_name_detail(tmp_path: Path):
    """Apply should write cargo_name_detail to the release_batches row."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["cargo_name_detail"] == "印粉"
    conn.close()


# ── Test 6: apply writes quantity_tons ─────────────────────────────────

def test_apply_writes_quantity_tons(tmp_path: Path):
    """Apply should write quantity_tons to the release_batches row."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["quantity_tons"] == 3000
    conn.close()


# ── Test 7: release_batch_id not found → not_found ────────────────────

def test_release_batch_id_not_found(tmp_path: Path):
    """Nonexistent release_batch_id should return not_found, not raise."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, "nonexistent_batch", dry_run=True, db_path=db_path,
    )
    assert result.status == "not_found"
    assert "nonexistent_batch" in result.message


# ── Test 8: existing non-null fields not overwritten by default ────────

def test_existing_fields_not_overwritten_by_default(tmp_path: Path):
    """When allow_overwrite=False, existing non-null fields are skipped."""
    db_path = tmp_path / "test.db"
    db_raw = _create_test_db(db_path)

    # Pre-set contract_no to a different value
    db_raw.execute(
        "UPDATE release_batches SET contract_no=? WHERE id=?",
        ("EXISTING_CONTRACT", _BATCH_ID),
    )
    db_raw.commit()
    db_raw.close()

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    # contract_no should be in skipped_fields because it already has a value
    assert "contract_no" in result.skipped_fields
    assert result.skipped_fields["contract_no"]["existing"] == "EXISTING_CONTRACT"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["contract_no"] == "EXISTING_CONTRACT"  # unchanged
    conn.close()


# ── Test 9: allow_overwrite=True allows overwrite ──────────────────────

def test_allow_overwrite_writes_existing_fields(tmp_path: Path):
    """When allow_overwrite=True, existing non-null fields ARE overwritten."""
    db_path = tmp_path / "test.db"
    db_raw = _create_test_db(db_path)

    db_raw.execute(
        "UPDATE release_batches SET contract_no=? WHERE id=?",
        ("OLD_CONTRACT", _BATCH_ID),
    )
    db_raw.commit()
    db_raw.close()

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, allow_overwrite=True, db_path=db_path,
    )
    assert result.status == "applied"
    # contract_no should be applied, not skipped
    assert "contract_no" in result.applied_fields
    assert result.applied_fields["contract_no"] == "HNMC20260520-1X-1"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["contract_no"] == "HNMC20260520-1X-1"  # overwritten
    conn.close()


# ── Test 10: repeat apply is idempotent ─────────────────────────────────

def test_repeat_apply_is_idempotent(tmp_path: Path):
    """Applying the same candidate twice should not produce duplicate entries
    or alter already-written fields."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = _make_complete_candidate()

    # First apply
    r1 = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    assert r1.status == "applied"

    # Second apply — should be idempotent; may return "applied" or "no_op"
    r2 = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    # "no_op" means all fields already have the same values (idempotent)
    # "applied" is also fine (SQLite no-op UPDATE)
    assert r2.status in ("applied", "no_op")

    # Verify no duplicate rows
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) as c FROM release_batches WHERE id=?", (_BATCH_ID,)
    ).fetchone()["c"]
    assert count == 1

    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["order_identifier"] == "CGR20260520095954"
    assert row["contract_no"] == "HNMC20260520-1X-1"
    conn.close()


# ── Test 11: schema_missing_fields ─────────────────────────────────────

def test_schema_missing_fields(tmp_path: Path):
    """When release_batches lacks target columns, return schema_missing_fields."""
    db_path = tmp_path / "test_no_cols.db"
    _create_test_db(db_path, include_new_columns=False)

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=True, db_path=db_path,
    )
    assert result.status == "schema_missing_fields"
    # At minimum, order_identifier and cargo_name_detail should be missing
    missing = result.schema_missing_fields
    assert "order_identifier" in missing
    assert "cargo_name_detail" in missing


# ── Test 12: does not modify wagon_shipments ───────────────────────────

def test_does_not_modify_wagon_shipments(tmp_path: Path):
    """Applying enrich_release_batch must not touch wagon_shipments table."""
    db_path = tmp_path / "test.db"
    db_raw = _create_test_db(db_path)

    # Create a wagon_shipments table
    db_raw.execute("""
        CREATE TABLE IF NOT EXISTS wagon_shipments (
            id TEXT PRIMARY KEY,
            batch_id TEXT,
            car_no TEXT
        )
    """)
    db_raw.execute(
        "INSERT INTO wagon_shipments (id, batch_id, car_no) VALUES (?, ?, ?)",
        ("ws_001", _BATCH_ID, "1625630"),
    )
    db_raw.commit()
    db_raw.close()

    candidate = _make_complete_candidate()

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    assert result.status == "applied"

    # Verify wagon_shipments is untouched
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wagon_shipments WHERE id='ws_001'").fetchone()
    assert row is not None
    assert row["car_no"] == "1625630"
    conn.close()


# ── Additional: source_message_id and source_group_id ──────────────────

def test_source_metadata_written(tmp_path: Path):
    """source_message_id and source_group_id from candidate are written."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = FreightDetailCandidate(
        message_id="wx_test_123",
        group_id="GROUP_TEST",
        message_time="2026-05-29T10:00:00Z",
        raw_text="订单标识CGR20260601000001 放货测试",
        order_identifier="CGR20260601000001",
        status="complete",
    )

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=False, db_path=db_path,
    )
    assert result.status == "applied"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)).fetchone()
    assert row["source_message_id"] == "wx_test_123"
    assert row["source_group_id"] == "GROUP_TEST"
    conn.close()


# ── Edge: no_match candidate → no_op ───────────────────────────────────

def test_no_match_candidate_returns_no_op(tmp_path: Path):
    """Candidate with status='no_match' should return no_op."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)

    candidate = FreightDetailCandidate(
        message_id="", group_id="", message_time="", raw_text="",
        status="no_match",
    )

    result = enrich_release_batch_with_freight_detail(
        candidate, _BATCH_ID, dry_run=True, db_path=db_path,
    )
    assert result.status == "no_op"
    assert result.planned_updates == {}


# ── #96: auto-linkage by CGR/HNMC → cargo_product_name ─────────────────

from sop_hub.sop.enrich_release_batch import (  # noqa: E402
    auto_enrich_release_batches_from_freight_detail,
)


def _set_order_identifier(db_path, cgr: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE release_batches SET order_identifier=? WHERE id=?",
        (cgr, _BATCH_ID),
    )
    conn.commit()
    conn.close()


def test_auto_enrich_matches_by_cgr_and_sets_cargo_product_name(tmp_path: Path):
    """A freight-detail candidate auto-matches the batch carrying its CGR and
    fills cargo_product_name (印粉) — no manual release_batch_id needed."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)
    _set_order_identifier(db_path, "CGR20260520095954")  # match key

    candidate = _make_complete_candidate()
    res = auto_enrich_release_batches_from_freight_detail(
        candidate, apply=True, db_path=db_path,
    )

    assert res["status"] == "applied"
    assert res["matched_by"] == "order_identifier"
    assert res["matched_count"] == 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM release_batches WHERE id=?", (_BATCH_ID,)
    ).fetchone()
    conn.close()
    assert row["cargo_product_name"] == "印粉"


def test_auto_enrich_does_not_overwrite_existing_product(tmp_path: Path):
    """Existing cargo_product_name is preserved when allow_overwrite=False."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)
    _set_order_identifier(db_path, "CGR20260520095954")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE release_batches SET cargo_product_name='麦克粉' WHERE id=?",
        (_BATCH_ID,),
    )
    conn.commit()
    conn.close()

    candidate = _make_complete_candidate()  # would set 印粉
    res = auto_enrich_release_batches_from_freight_detail(
        candidate, apply=True, db_path=db_path,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT cargo_product_name FROM release_batches WHERE id=?", (_BATCH_ID,)
    ).fetchone()
    conn.close()
    assert row["cargo_product_name"] == "麦克粉"  # not overwritten
    # cargo_product_name was skipped, not applied
    assert all(
        "cargo_product_name" not in r["applied"] for r in res["results"]
    )


def test_auto_enrich_no_release_batch_with_key_returns_no_match(tmp_path: Path):
    """If no batch carries the candidate's CGR/HNMC, status is no_match."""
    db_path = tmp_path / "test.db"
    _create_test_db(db_path)  # row has no matching order_identifier/contract_no

    candidate = _make_complete_candidate()
    res = auto_enrich_release_batches_from_freight_detail(
        candidate, apply=True, db_path=db_path,
    )
    assert res["status"] == "no_match"
    assert res["results"] == []
