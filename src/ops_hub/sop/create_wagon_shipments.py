"""Create wagon_shipments from 95306 shipment candidates — R45.

The final P0 executor in the departure_flow chain:

  departure_text → parse → QueryWindow → 95306 query → candidates
  → create_wagon_shipments → write to sop_agent.db

Supports repeated departures for the same ship/release_batch:
  蓝鳍 first trip 18 cars, second trip 28 cars → cumulative 46 cars.

This module:
- reads sop_agent.db (release_batches + wagon_shipments)
- reads 95306_collection.sqlite3 (shipment_release_batch_matches, read-only)
- writes sop_agent.db (INSERT wagon_shipments) ONLY with dry_run=False
- writes sop_agent.db (INSERT shipment_release_batch_matches) — local table
- updates release_batches.actual_wagon_count + dispatch_status
- does NOT write to 95306 DB — 95306 is read-only
- does NOT modify 95306 shipments table
- does NOT send messages, generate Excel/JSON, or modify SOP YAML
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_hub.sop.departure_text_parser import DepartureCandidate
from ops_hub.sop.shipment_query_window import ShipmentQueryResult, ShipmentCandidate


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class WagonPlan:
    """A single wagon_shipment to create."""

    ydid: str
    wagon_no: str
    waybill_no: str = ""
    container_no: str = ""
    origin_station: str = ""
    destination_station: str = ""
    cargo_name: str = ""
    ticketed_at: str = ""
    departed_at: str = ""
    arrived_at: str = ""
    delivered_at: str = ""
    current_status: str = ""
    action: str = "pending"  # insert | skip_existing | conflict_other_batch | filtered_out


@dataclass
class CreateWagonShipmentsRequest:
    """Request to create wagon_shipments from 95306 candidates.

    Fields:
      release_batch_id: target release_batches.id.
      departure_candidate: parsed departure text.
      shipment_query_result: result from query_95306_shipments_by_window.
      allow_partial: if True, allow fewer candidates than expected car_count.
      allow_existing_skip: skip wagons already in this release_batch.
    """

    release_batch_id: str
    departure_candidate: DepartureCandidate
    shipment_query_result: ShipmentQueryResult
    allow_partial: bool = False
    allow_existing_skip: bool = True


@dataclass
class CreateWagonShipmentsResult:
    """Result of a create_wagon_shipments operation.

    Fields:
      status: "safe_to_apply" | "pending_review" | "not_found" | "no_candidates"
      safe_to_apply: True if all automatic rules pass.
      planned_insert_count: number of wagons to insert.
      inserted_count: wagons actually inserted (apply mode).
      skipped_existing_count: wagons already in this release_batch.
      conflict_count: wagons in other release_batches.
      expected_car_count: from departure_candidate.car_count.
      candidate_count: from shipment_query_result.total_candidates.
      warnings: list of human-readable warning strings.
      schema_missing_fields: columns/table names absent from DB.
      plans: per-wagon WagonPlan list.
      release_batch_progress: fields to update on release_batches.
    """

    status: str
    safe_to_apply: bool = False
    planned_insert_count: int = 0
    inserted_count: int = 0
    skipped_existing_count: int = 0
    conflict_count: int = 0
    expected_car_count: int = -1
    candidate_count: int = 0
    warnings: list[str] = field(default_factory=list)
    schema_missing_fields: list[str] = field(default_factory=list)
    plans: list[WagonPlan] = field(default_factory=list)
    release_batch_progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "safe_to_apply": self.safe_to_apply,
            "planned_insert_count": self.planned_insert_count,
            "inserted_count": self.inserted_count,
            "skipped_existing_count": self.skipped_existing_count,
            "conflict_count": self.conflict_count,
            "expected_car_count": self.expected_car_count,
            "candidate_count": self.candidate_count,
            "warnings": list(self.warnings),
            "schema_missing_fields": list(self.schema_missing_fields),
            "plans": [
                {
                    "wagon_no": p.wagon_no,
                    "waybill_no": p.waybill_no,
                    "container_no": p.container_no,
                    "action": p.action,
                }
                for p in self.plans
            ],
            "release_batch_progress": self.release_batch_progress,
        }


# ── Helpers ─────────────────────────────────────────────────────────────

def _generate_departure_id(release_batch_id: str, ship_name: str) -> str:
    """Generate a deterministic departure_id from release_batch + ship."""
    return hashlib.sha1(f"{release_batch_id}|{ship_name}".encode()).hexdigest()[:16]


def _gen_wagon_id(ydid: str, release_batch_id: str) -> str:
    """Generate a deterministic wagon_shipments.id."""
    return hashlib.sha1(f"{ydid}|{release_batch_id}".encode()).hexdigest()[:24]


def _resolve_sop_db_path(db_path: str | Path | None = None) -> Path:
    import os
    if db_path:
        return Path(db_path)
    env = os.environ.get("BUSINESS_DATA_AGENT_DB_PATH")
    if env:
        return Path(env)
    return Path.cwd() / "data" / "sop_agent.db"


def _resolve_rail_db_path(rail_db_path: str | Path | None = None) -> Path:
    import os
    if rail_db_path:
        return Path(rail_db_path)
    env = os.environ.get("OPS_HUB_DB_95306_PATH") or os.environ.get("DB_95306_PATH")
    if env:
        return Path(env)
    return (
        Path.home()
        / "projects" / "repos" / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
    )


def _filter_by_station_cargo(
    candidates: list[ShipmentCandidate],
    departure: DepartureCandidate,
) -> list[ShipmentCandidate]:
    """Narrow candidates by origin/destination/cargo match with departure context.

    When candidate_count > expected car_count, this filters to the best-matching
    subset using station and cargo similarity heuristics.
    """
    dest = departure.destination
    if not dest:
        return candidates

    # Prefer candidates whose destination matches the departure
    matched = [c for c in candidates if dest in (c.destination_station or "")]
    if matched and len(matched) <= len(candidates):
        return matched

    # Also try cargo match as tiebreaker
    if departure.raw_text:
        cargo_keywords = _cargo_keywords(departure.raw_text)
        if cargo_keywords:
            cargo_matches = [
                c for c in candidates
                if any(kw in (c.cargo_name or "") for kw in cargo_keywords)
            ]
            if cargo_matches and len(cargo_matches) < len(candidates):
                return cargo_matches

    return candidates


def _cargo_keywords(raw_text: str) -> list[str]:
    """Extract cargo keywords from departure text."""
    keywords = []
    if "镍" in raw_text:
        keywords.append("镍")
    if "铁" in raw_text:
        keywords.append("铁")
    if "矿" in raw_text:
        keywords.append("矿")
    if "粉" in raw_text:
        keywords.append("粉")
    return keywords


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


# ── Core executor ────────────────────────────────────────────────────────

def create_wagon_shipments_from_candidates(
    *,
    release_batch_id: str,
    departure_candidate: DepartureCandidate,
    shipment_query_result: ShipmentQueryResult,
    dry_run: bool = True,
    allow_partial: bool = False,
    allow_existing_skip: bool = True,
    db_path: str | Path | None = None,
    rail_db_path: str | Path | None = None,
) -> CreateWagonShipmentsResult:
    """Create wagon_shipments rows from 95306 shipment candidates.

    Args:
      release_batch_id: target release_batches.id.
      departure_candidate: parsed departure text.
      shipment_query_result: from query_95306_shipments_by_window.
      dry_run: if True, plan only, no DB writes.
      allow_partial: if True, accept fewer candidates than car_count.
      allow_existing_skip: if True, skip already-existing wagons.
      db_path: path to sop_agent.db.
      rail_db_path: path to 95306_collection.sqlite3.

    Returns:
      CreateWagonShipmentsResult.
    """
    sop_path = _resolve_sop_db_path(db_path)

    result = CreateWagonShipmentsResult(
        status="no_candidates",
        expected_car_count=departure_candidate.car_count,
        candidate_count=shipment_query_result.total_candidates,
    )

    # ── 1. Check release_batch exists ────────────────────────────────
    sop_conn = sqlite3.connect(str(sop_path))
    sop_conn.row_factory = sqlite3.Row
    try:
        rb = sop_conn.execute(
            "SELECT * FROM release_batches WHERE id = ?", (release_batch_id,)
        ).fetchone()
        if rb is None:
            result.status = "not_found"
            result.warnings.append(f"release_batch_id not found: {release_batch_id}")
            return result
        ship_name = rb["ship_name"] or ""

        # ── 2. Get existing wagon_shipments for this batch ───────────
        existing_ws: dict[str, str] = {}  # key → wagon_id
        existing_wagon_keys: set[str] = set()
        try:
            for row in sop_conn.execute(
                "SELECT id, car_no, waybill_no FROM wagon_shipments WHERE batch_id = ?",
                (release_batch_id,),
            ):
                existing_ws[row["id"]] = row["car_no"] or ""
                existing_wagon_keys.add(row["car_no"] or "")
        except sqlite3.OperationalError:
            pass  # wagon_shipments table doesn't exist yet

        # ── 3. Get all existing wagon_shipments (cross-batch conflict check) ──
        existing_cross_batch: dict[str, str] = {}  # wagon_no → batch_id
        try:
            for row in sop_conn.execute(
                "SELECT car_no, batch_id FROM wagon_shipments WHERE car_no IS NOT NULL AND car_no != ''"
            ):
                car = row["car_no"]
                if car and row["batch_id"] != release_batch_id:
                    existing_cross_batch[car] = row["batch_id"]
        except sqlite3.OperationalError:
            pass

        # ── 4. Check wagon_shipments schema ──────────────────────────
        ws_columns: set[str] = set()
        try:
            ws_columns = {
                r[1] for r in sop_conn.execute("PRAGMA table_info(wagon_shipments)").fetchall()
            }
        except sqlite3.OperationalError:
            result.schema_missing_fields.append("table:wagon_shipments")
            result.status = "schema_missing"
            return result

        required_cols = [
            "id", "departure_id", "batch_id", "car_no", "car_model", "cargo_name",
            "origin_name", "destination_name", "ticketed_at", "departed_at",
            "arrived_at", "delivered_at", "confirmed_received_at",
            "container_no", "waybill_no", "project_id", "ship_name",
            "dispatch_status", "source_message_id", "source_group_id",
        ]
        for col in required_cols:
            if col not in ws_columns:
                result.schema_missing_fields.append(f"column:wagon_shipments.{col}")

        # ── 5. Get candidates and filter ─────────────────────────────
        candidates = list(shipment_query_result.candidates)
        expected = departure_candidate.car_count

        # ── 5a. No candidates → early return ────────────────────────
        if len(candidates) == 0:
            result.status = "no_candidates"
            result.safe_to_apply = False
            result.warnings.append("No candidates in query result")
            return result

        # ── 5b. If count > expected, try filtering ──────────────────
        if len(candidates) > expected and expected > 0:
            candidates = _filter_by_station_cargo(candidates, departure_candidate)

        # ── 5b. Build per-wagon plans ───────────────────────────────
        plans: list[WagonPlan] = []
        for c in candidates:
            plan = WagonPlan(
                ydid=c.ydid,
                wagon_no=c.wagon_no,
                waybill_no=c.waybill_no,
                container_no=c.container_no,
                origin_station=c.origin_station,
                destination_station=c.destination_station,
                cargo_name=c.cargo_name,
                ticketed_at=c.ticketed_at,
                departed_at=c.departed_at,
                arrived_at=c.arrived_at,
                delivered_at=c.delivered_at,
                current_status=c.current_status,
            )

            # ── Check existing same-batch ────────────────────────────
            if allow_existing_skip and plan.wagon_no in existing_wagon_keys:
                plan.action = "skip_existing"
                result.skipped_existing_count += 1
                plans.append(plan)
                continue

            # ── Check cross-batch conflict ───────────────────────────
            if plan.wagon_no in existing_cross_batch:
                plan.action = "conflict_other_batch"
                result.conflict_count += 1
                plans.append(plan)
                continue

            plan.action = "insert"
            plans.append(plan)

        result.plans = plans
        insert_plans = [p for p in plans if p.action == "insert"]
        result.planned_insert_count = len(insert_plans)

        # ── 6. Determine status ──────────────────────────────────────
        if result.planned_insert_count == expected and expected > 0:
            result.status = "safe_to_apply"
            result.safe_to_apply = True
        elif result.planned_insert_count + result.skipped_existing_count == expected:
            result.status = "safe_to_apply"
            result.safe_to_apply = True
            result.warnings.append(
                f"Planned insert ({result.planned_insert_count}) + "
                f"existing ({result.skipped_existing_count}) = expected ({expected})"
            )
        elif result.planned_insert_count > expected and expected > 0:
            result.status = "pending_review"
            result.safe_to_apply = False
            result.warnings.append(
                f"Planned insert ({result.planned_insert_count}) > "
                f"expected ({expected}). Filtering may be needed."
            )
        elif result.planned_insert_count < expected and expected > 0:
            if allow_partial:
                result.status = "safe_to_apply"
                result.safe_to_apply = True
                result.warnings.append(
                    f"Partial: {result.planned_insert_count}/{expected} cars. "
                    f"allow_partial=True accepted."
                )
            else:
                result.status = "pending_review"
                result.safe_to_apply = False
                result.warnings.append(
                    f"Planned insert ({result.planned_insert_count}) < "
                    f"expected ({expected}). Set allow_partial=True to proceed."
                )
        else:
            # expected == -1 or 0 (no car count specified)
            result.status = "safe_to_apply"
            result.safe_to_apply = True

        # ── 7. Release batch progress fields ─────────────────────────
        rb_columns = {
            r[1] for r in sop_conn.execute("PRAGMA table_info(release_batches)").fetchall()
        }
        current_wagon_count = int(rb["actual_wagon_count"] or 0)
        new_total = current_wagon_count + result.planned_insert_count
        result.release_batch_progress = {
            "actual_wagon_count": new_total,
        }
        if "dispatch_status" in rb_columns:
            result.release_batch_progress["dispatch_status"] = "in_progress"
            result.release_batch_progress["dispatch_status_updated_at"] = (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            )

        # ── 8. Dry run → return ─────────────────────────────────────
        if dry_run:
            return result

        # ── 9. Apply: write to sop_agent.db ──────────────────────────
        inserted = 0
        departure_id = _generate_departure_id(release_batch_id, ship_name)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for plan in insert_plans:
            wagon_id = _gen_wagon_id(plan.ydid, release_batch_id)
            try:
                sop_conn.execute(
                    """INSERT INTO wagon_shipments (
                        id, departure_id, batch_id, car_no, car_model,
                        cargo_name, origin_name, destination_name,
                        ticketed_at, departed_at, arrived_at,
                        delivered_at, confirmed_received_at,
                        container_no, waybill_no,
                        project_id, ship_name, dispatch_status,
                        source_message_id, source_group_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        wagon_id, departure_id, release_batch_id,
                        plan.wagon_no, "",
                        plan.cargo_name, plan.origin_station, plan.destination_station,
                        plan.ticketed_at, plan.departed_at, plan.arrived_at,
                        plan.delivered_at, "",
                        plan.container_no, plan.waybill_no,
                        departure_candidate.project_id, ship_name, "pending",
                        departure_candidate.message_id, departure_candidate.group_id,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                result.warnings.append(f"Duplicate wagon: {plan.wagon_no} (ydid={plan.ydid})")
                continue

        sop_conn.commit()
        result.inserted_count = inserted

        # ── 10. Update release_batches progress ──────────────────────
        set_clauses = []
        params: dict[str, Any] = {"id": release_batch_id}
        for field, val in result.release_batch_progress.items():
            if field in rb_columns:
                set_clauses.append(f"{field} = :{field}")
                params[field] = val
        if set_clauses:
            sql = f"UPDATE release_batches SET {', '.join(set_clauses)} WHERE id = :id"
            sop_conn.execute(sql, params)
            sop_conn.commit()

        # ── 11. Write shipment_release_batch_matches to sop_agent.db ─
        # Ensure the table exists
        sop_conn.execute("""
            CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
                id TEXT PRIMARY KEY,
                release_batch_id TEXT NOT NULL,
                wagon_shipment_id TEXT NOT NULL,
                ydid TEXT NOT NULL,
                waybill_no TEXT DEFAULT '',
                wagon_no TEXT NOT NULL,
                container_no TEXT DEFAULT '',
                match_source TEXT DEFAULT 'departure_text_match',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for plan in insert_plans:
            match_id = hashlib.sha1(
                f"{release_batch_id}|{plan.ydid}".encode()
            ).hexdigest()[:24]
            wagon_id = _gen_wagon_id(plan.ydid, release_batch_id)
            try:
                sop_conn.execute(
                    """INSERT INTO shipment_release_batch_matches (
                        id, release_batch_id, wagon_shipment_id,
                        ydid, waybill_no, wagon_no, container_no,
                        match_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        match_id, release_batch_id, wagon_id,
                        plan.ydid, plan.waybill_no, plan.wagon_no,
                        plan.container_no, "departure_text_match",
                    ),
                )
            except sqlite3.IntegrityError:
                pass  # already exists — idempotent
        sop_conn.commit()

        return result

    finally:
        sop_conn.close()
