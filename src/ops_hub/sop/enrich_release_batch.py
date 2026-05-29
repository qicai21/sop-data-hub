"""Enrich release_batch with freight_detail_candidate via manual binding.

R41: Manual binding executor for FreightDetailCandidate → release_batch.

Business rule:
  freight_detail_text typically has no ship name, so the system cannot
  auto-bind to a specific vessel or release batch.  An operator / Agent
  must explicitly specify the release_batch_id.

This module:
- accepts a FreightDetailCandidate + release_batch_id;
- validates the release_batch exists;
- checks which target columns are available in the release_batches table;
- in dry_run mode, returns planned_updates without writing;
- in apply mode, writes non-empty fields to the release_batches row;
- respects allow_overwrite to control whether existing non-null fields
  are overwritten;
- is idempotent (repeating the same apply yields the same result);
- reports schema_missing_fields when target columns are absent;
- does NOT query 95306, modify wagon_shipments, generate Excel/JSON,
  send messages, or change any SOP YAML.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_hub.sop.freight_detail_extractor import FreightDetailCandidate


# ── Target columns in release_batches ────────────────────────────────────
# Fields that are always present (defined in CREATE TABLE).
_ALWAYS_PRESENT_COLUMNS = frozenset({"contract_no", "updated_at"})

# Fields that may be absent (added via migration); checked at runtime.
_MIGRATION_COLUMNS = frozenset({
    "order_identifier",
    "cargo_name_detail",
    "quantity_tons",
    "source_message_id",
    "source_group_id",
})

_ALL_TARGET_COLUMNS = _ALWAYS_PRESENT_COLUMNS | _MIGRATION_COLUMNS


# ── Data model ────────────────────────────────────────────────────────────

@dataclass
class ReleaseBatchBindingRequest:
    """A request to bind a freight_detail_candidate to a release_batch.

    Fields:
      freight_detail_candidate: the parsed freight detail text result.
      release_batch_id: the target release_batches.id to bind to.
      allow_overwrite: if True, overwrite existing non-null fields.
    """

    freight_detail_candidate: FreightDetailCandidate
    release_batch_id: str
    allow_overwrite: bool = False


@dataclass
class ReleaseBatchEnrichmentResult:
    """Result of a release_batch enrichment operation.

    Fields:
      status: "applied" | "dry_run" | "not_found" | "schema_missing_fields" | "no_op"
      planned_updates: fields that would be written (dry_run) or were written (applied).
      schema_missing_fields: target columns absent from the release_batches table.
      applied_fields: fields actually written (apply mode only).
      skipped_fields: fields skipped because existing value is non-null and
                      allow_overwrite=False.
      message: human-readable summary.
    """

    status: str
    planned_updates: dict[str, Any] = field(default_factory=dict)
    schema_missing_fields: list[str] = field(default_factory=list)
    applied_fields: dict[str, Any] = field(default_factory=dict)
    skipped_fields: dict[str, Any] = field(default_factory=dict)
    message: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────

def _resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the sop_agent.db path, respecting BUSINESS_DATA_AGENT_DB_PATH env."""
    import os
    if db_path:
        return Path(db_path)
    env_path = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env_path:
        return Path(env_path)
    try:
        from ops_hub.config import load_settings
        return Path(load_settings().agent_db_path)
    except Exception:
        return Path.cwd() / "data" / "sop_agent.db"


def _open_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the sop_agent database directly (no migration).

    We open a raw connection so the schema check can detect missing
    columns before any migration runs.  In production, the DB is
    already migrated — this executor just reads/writes the existing
    schema.
    """
    resolved = _resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    return conn


def _get_available_columns(conn: sqlite3.Connection) -> set[str]:
    """Return the set of column names in the release_batches table."""
    rows = conn.execute("PRAGMA table_info(release_batches)").fetchall()
    return {row["name"] for row in rows}


def _is_non_empty(value: Any) -> bool:
    """Check if a value is non-null and non-empty (for strings / numbers)."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


# ── Core executor ────────────────────────────────────────────────────────

def enrich_release_batch_with_freight_detail(
    freight_detail_candidate: FreightDetailCandidate,
    release_batch_id: str,
    *,
    dry_run: bool = True,
    allow_overwrite: bool = False,
    db_path: str | Path | None = None,
) -> ReleaseBatchEnrichmentResult:
    """Enrich a release_batch row with fields from a FreightDetailCandidate.

    Args:
      freight_detail_candidate: the parsed freight detail text result.
      release_batch_id: the target release_batches.id to bind to.
      dry_run: if True (default), compute planned_updates but do not write.
      allow_overwrite: if True, overwrite existing non-null fields.
      db_path: optional path to the sop_agent DB.

    Returns:
      ReleaseBatchEnrichmentResult with status indicating the outcome.
    """
    # ── Guard: candidate must be complete ──────────────────────────────
    if freight_detail_candidate.status not in ("complete", "incomplete"):
        return ReleaseBatchEnrichmentResult(
            status="no_op",
            message=(
                f"Candidate status is '{freight_detail_candidate.status}', "
                f"not 'complete' or 'incomplete'.  Nothing to bind."
            ),
        )

    # ── Open DB ───────────────────────────────────────────────────────
    conn = _open_db(db_path)
    try:
        available = _get_available_columns(conn)

        # ── Check schema: which target columns are missing? ───────────
        missing = sorted(
            col for col in _ALL_TARGET_COLUMNS if col not in available
        )
        if set(missing) - {"updated_at"}:
            # Allow updated_at to be missing (should not happen, but safe)
            return ReleaseBatchEnrichmentResult(
                status="schema_missing_fields",
                schema_missing_fields=missing,
                message=f"Missing column(s) in release_batches: {', '.join(missing)}",
            )

        # ── Check release_batch exists ───────────────────────────────
        row = conn.execute(
            "SELECT * FROM release_batches WHERE id = ?", (release_batch_id,)
        ).fetchone()
        if row is None:
            return ReleaseBatchEnrichmentResult(
                status="not_found",
                message=f"release_batch_id not found: {release_batch_id}",
            )

        # ── Build field mapping: candidate field → DB column ─────────
        # Maps FreightDetailCandidate fields to release_batches columns.
        field_mapping: dict[str, str] = {
            "order_identifier": "order_identifier",
            "contract_no": "contract_no",
            "cargo_name_detail": "cargo_name_detail",
            "quantity_tons": "quantity_tons",
            "message_id": "source_message_id",
            "group_id": "source_group_id",
        }

        # ── Compute planned_updates ──────────────────────────────────
        planned_updates: dict[str, Any] = {}
        skipped_fields: dict[str, Any] = {}
        applied_fields: dict[str, Any] = {}

        for candidate_field, db_column in field_mapping.items():
            if db_column not in available:
                continue  # already guarded above, but be safe

            new_value = getattr(freight_detail_candidate, candidate_field, None)
            if not _is_non_empty(new_value):
                continue  # skip empty values

            existing_value = row[db_column]

            if _is_non_empty(existing_value) and not allow_overwrite:
                skipped_fields[db_column] = {
                    "existing": existing_value,
                    "candidate": new_value,
                }
                continue

            planned_updates[db_column] = new_value

        if not planned_updates:
            return ReleaseBatchEnrichmentResult(
                status="no_op",
                planned_updates={},
                skipped_fields=skipped_fields,
                message="No fields to update (all values already set or candidate has no data).",
            )

        # ── Dry-run: return planned_updates only ─────────────────────
        if dry_run:
            return ReleaseBatchEnrichmentResult(
                status="dry_run",
                planned_updates=planned_updates,
                skipped_fields=skipped_fields,
                message=f"Dry-run: {len(planned_updates)} field(s) would be updated.",
            )

        # ── Apply: write to DB ──────────────────────────────────────
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        applied_updates = dict(planned_updates)

        set_clauses = []
        params: dict[str, Any] = {"id": release_batch_id}

        for db_column, value in planned_updates.items():
            param_name = f"val_{db_column}"
            set_clauses.append(f"{db_column} = :{param_name}")
            params[param_name] = value

        # Always update updated_at
        set_clauses.append("updated_at = :updated_at")
        params["updated_at"] = now

        sql = f"UPDATE release_batches SET {', '.join(set_clauses)} WHERE id = :id"
        conn.execute(sql, params)
        conn.commit()

        return ReleaseBatchEnrichmentResult(
            status="applied",
            planned_updates=applied_updates,
            applied_fields=applied_updates,
            skipped_fields=skipped_fields,
            message=f"Applied {len(applied_updates)} field(s) to release_batch_id={release_batch_id}.",
        )

    finally:
        conn.close()
