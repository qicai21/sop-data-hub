"""Sync 95306 shipment status snapshots into sop_agent.db wagon_shipments.

Reads wagon_shipments for a project + ship_name, queries 95306_collection.sqlite3
for latest status, and writes departed_at / arrived_at / delivered_at back.

Matching priority: ydid → car_no + destination_name.
Status mapping: 已发车→dispatched, 到站→arrived, 交付→delivered.

This module:
- reads sop_agent.db (wagon_shipments + release_batches)
- reads 95306_collection.sqlite3 (shipments table, read-only)
- writes sop_agent.db (UPDATE wagon_shipments) ONLY with --apply
- does NOT write 95306 DB, modify YAML, generate Excel, or send reports
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Status mapping ─────────────────────────────────────────────────────

STATUS_TO_FIELDS: dict[str, list[str]] = {
    "发车": ["departed_at"],
    "已发车": ["departed_at"],
    "到站": ["departed_at", "arrived_at"],
    "已到站": ["departed_at", "arrived_at"],
    "交付": ["departed_at", "arrived_at", "delivered_at"],
    "货物已交付": ["departed_at", "arrived_at", "delivered_at"],
    "已交付": ["departed_at", "arrived_at", "delivered_at"],
    # 用户口径：确认收货 / 已卸车 亦视为交付完成
    "确认收货": ["departed_at", "arrived_at", "delivered_at"],
    "已卸车": ["departed_at", "arrived_at", "delivered_at"],
}

STAGE_TO_DISPATCH_STATUS: dict[str, str] = {
    "交付": "delivered",
    "货物已交付": "delivered",
    "已交付": "delivered",
    "确认收货": "delivered",
    "已卸车": "delivered",
    "到站": "arrived",
    "已到站": "arrived",
    "发车": "dispatched",
    "已发车": "dispatched",
}

# R39: Default rule — if SOP does not declare additional manual confirmation,
# 95306 "交付" status implies confirmed_received.
# This can be overridden by SOP YAML in the future.
DEFAULT_CONFIRMED_RECEIVED_RULE = (
    "If all wagons are delivered per 95306 and SOP does not require "
    "manual confirmation, auto-set confirmed_received_at = delivered_at."
)


@dataclass
class WagonRow:
    """A wagon_shipments row to sync."""
    db_id: str
    batch_id: str
    car_no: str
    ydid: str = ""
    destination_name: str = ""
    departed_at: str = ""
    arrived_at: str = ""
    delivered_at: str = ""
    status_name: str = ""
    dispatch_status: str = ""


@dataclass
class ShipmentSnapshot:
    """A matching shipment row from 95306 DB."""
    ydid: str
    car_no: str
    destination_name: str
    ticketed_at: str = ""
    departed_at: str = ""
    arrived_at: str = ""
    delivered_at: str = ""
    status_name: str = ""
    latest_stage_name: str = ""


@dataclass
class UpdatePlan:
    """A planned update for one wagon."""
    db_id: str
    car_no: str
    field: str
    old_value: str
    new_value: str
    source: str  # "95306"
    reason: str = ""


@dataclass
class SyncResult:
    """Result of a sync operation."""

    project_id: str
    ship_name: str
    dry_run: bool

    total_wagons: int = 0
    matched_count: int = 0
    unmatched_count: int = 0
    update_count: int = 0

    departed_update_count: int = 0
    arrived_update_count: int = 0
    delivered_update_count: int = 0

    schema_missing_fields: list[str] = field(default_factory=list)
    batch_level_suggestion: str = ""
    batch_dispatch_status: str = ""

    updates: list[UpdatePlan] = field(default_factory=list)
    unmatched_wagons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "ship_name": self.ship_name,
            "dry_run": self.dry_run,
            "total_wagons": self.total_wagons,
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
            "update_count": self.update_count,
            "departed_update_count": self.departed_update_count,
            "arrived_update_count": self.arrived_update_count,
            "delivered_update_count": self.delivered_update_count,
            "schema_missing_fields": list(self.schema_missing_fields),
            "batch_level_suggestion": self.batch_level_suggestion,
            "batch_dispatch_status": self.batch_dispatch_status,
            "updates": [
                {
                    "db_id": u.db_id,
                    "car_no": u.car_no,
                    "field": u.field,
                    "old_value": u.old_value,
                    "new_value": u.new_value,
                    "source": u.source,
                    "reason": u.reason,
                }
                for u in self.updates
            ],
            "unmatched_wagons": list(self.unmatched_wagons),
        }


class ShipmentStatusSync:
    """Sync 95306 shipment snapshots into sop_agent.db."""

    def __init__(
        self,
        sop_db_path: str | Path,
        rail_db_path: str | Path,
    ):
        self.sop_db_path = Path(sop_db_path)
        self.rail_db_path = Path(rail_db_path)

    def sync(
        self,
        *,
        project_id: str = "",
        ship_name: str = "",
        dry_run: bool = True,
    ) -> SyncResult:
        """Sync shipment status from 95306 DB into sop_agent.db.

        Args:
          project_id: filter by project_id.
          ship_name: filter by ship_name.
          dry_run: if True, only plan updates, don't write.

        Returns:
          SyncResult with full plan and statistics.
        """
        result = SyncResult(
            project_id=project_id,
            ship_name=ship_name,
            dry_run=dry_run,
        )

        # ── 1. Load wagon_shipments ───────────────────────────────────
        wagons = self._load_wagons(project_id=project_id, ship_name=ship_name)
        result.total_wagons = len(wagons)
        if not wagons:
            return result

        # ── 2. Check schema ──────────────────────────────────────────
        sop_cols = self._sop_wagon_columns()
        for col in ("departed_at", "arrived_at", "delivered_at"):
            if col not in sop_cols:
                result.schema_missing_fields.append(col)

        # ── 3. Query 95306 for each wagon ────────────────────────────
        updates: list[UpdatePlan] = []
        unmatched: list[str] = []
        wagons_updated: set[str] = set()

        for wagon in wagons:
            snapshot = self._find_snapshot(wagon)
            if snapshot is None:
                unmatched.append(wagon.car_no)
                continue

            # Map fields from snapshot
            stage = snapshot.latest_stage_name or snapshot.status_name or ""
            fields_to_update = STATUS_TO_FIELDS.get(stage, [])

            for field in fields_to_update:
                new_value = getattr(snapshot, field, "") or ""
                old_value = getattr(wagon, field, "") or ""

                if not new_value:
                    continue

                # Don't overwrite existing non-NULL values
                if old_value and old_value.strip():
                    continue

                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                    source="95306",
                    reason=f"95306 status={stage}",
                ))

                # Count by field
                if field == "departed_at":
                    result.departed_update_count += 1
                elif field == "arrived_at":
                    result.arrived_update_count += 1
                elif field == "delivered_at":
                    result.delivered_update_count += 1

            wagons_updated.add(wagon.db_id)

            # Also plan dispatch_status update for the batch
            new_dispatch = STAGE_TO_DISPATCH_STATUS.get(stage, "")
            if new_dispatch and new_dispatch != wagon.dispatch_status:
                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field="dispatch_status",
                    old_value=wagon.dispatch_status,
                    new_value=new_dispatch,
                    source="95306",
                    reason=f"95306 stage={stage}",
                ))

        result.updates = updates
        result.unmatched_wagons = unmatched
        result.update_count = len(updates)

        # Count matched wagons (those with at least one update OR successfully found)
        result.matched_count = sum(
            1 for w in wagons
            if any(u.db_id == w.db_id for u in updates)
        )
        result.unmatched_count = result.total_wagons - result.matched_count

        # ── 4. Batch-level suggestion ─────────────────────────────────
        delivered_wagons = sum(
            1 for u in updates if u.field == "delivered_at"
        )
        if delivered_wagons >= result.total_wagons:
            result.batch_level_suggestion = "confirmed_received_candidate"
            result.batch_dispatch_status = "delivered"
        elif result.arrived_update_count >= result.total_wagons:
            result.batch_level_suggestion = "arrived"
            result.batch_dispatch_status = "arrived"
        elif result.departed_update_count >= result.total_wagons:
            result.batch_level_suggestion = "dispatched"
            result.batch_dispatch_status = "dispatched"

        # ── 5. Apply writes ──────────────────────────────────────────
        if not dry_run:
            self._apply_updates(updates, project_id=project_id, ship_name=ship_name)

        return result

    # ── internal ──────────────────────────────────────────────────────

    def _load_wagons(
        self, *, project_id: str = "", ship_name: str = ""
    ) -> list[WagonRow]:
        """Load wagon_shipments joined with release_batches."""
        conn = sqlite3.connect(str(self.sop_db_path))
        conn.row_factory = sqlite3.Row
        try:
            sql = """
                SELECT ws.id, ws.batch_id, ws.car_no,
                       ws.departed_at, ws.arrived_at,
                       ws.destination_name, ws.status_name,
                       rb.dispatch_status, rb.project
                FROM wagon_shipments ws
                JOIN release_batches rb ON ws.batch_id = rb.id
                WHERE rb.ship_name = ?
            """
            params: list[Any] = [ship_name]
            sql += " ORDER BY ws.ticketed_at"

            rows = conn.execute(sql, params).fetchall()

            # Post-filter by project_id if provided.
            # The release_batches.project field may store legacy names
            # (e.g. "吉林金钢-锦州港铁矿发运项目") rather than SOP project_ids
            # (e.g. "jilin_jingang_jinzhou").
            if project_id:
                project_id_aliases = {project_id}
                if "jilin_jingang" in project_id:
                    project_id_aliases.add("吉林金钢-锦州港铁矿发运项目")
                rows = [
                    r for r in rows
                    if (r["project"] or "") in project_id_aliases
                ]

            return [
                WagonRow(
                    db_id=row["id"],
                    batch_id=row["batch_id"],
                    car_no=row["car_no"],
                    destination_name=row["destination_name"] or "",
                    departed_at=row["departed_at"] or "",
                    arrived_at=row["arrived_at"] or "",
                    delivered_at=ShipmentStatusSync._safe_col(row, "delivered_at"),
                    status_name=row["status_name"] or "",
                    dispatch_status=row["dispatch_status"] or "",
                )
                for row in rows
            ]
        finally:
            conn.close()

    def _sop_wagon_columns(self) -> set[str]:
        """Get wagon_shipments column names."""
        conn = sqlite3.connect(str(self.sop_db_path))
        try:
            rows = conn.execute("PRAGMA table_info(wagon_shipments)").fetchall()
            return {row[1] for row in rows}
        finally:
            conn.close()

    @staticmethod
    def _release_batch_columns(conn: sqlite3.Connection) -> set[str]:
        """Get release_batches column names."""
        try:
            rows = conn.execute("PRAGMA table_info(release_batches)").fetchall()
            return {row[1] for row in rows}
        except Exception:
            return set()

    def _find_snapshot(self, wagon: WagonRow) -> ShipmentSnapshot | None:
        """Find the matching 95306 shipment snapshot.

        Priority: ydid → car_no + destination_name.
        """
        conn = sqlite3.connect(str(self.rail_db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Prioritize: car_no + destination_name for this project
            if wagon.car_no and wagon.destination_name:
                row = conn.execute(
                    """
                    SELECT ydid, car_no, destination_name,
                           ticketed_at, departed_at, arrived_at, delivered_at,
                           status_name, latest_stage_name
                    FROM shipments
                    WHERE car_no = ? AND destination_name = ?
                    ORDER BY ticketed_at DESC
                    LIMIT 1
                    """,
                    (wagon.car_no, wagon.destination_name),
                ).fetchone()
                if row:
                    return self._row_to_snapshot(row)

            # Fallback: car_no only
            if wagon.car_no:
                row = conn.execute(
                    """
                    SELECT ydid, car_no, destination_name,
                           ticketed_at, departed_at, arrived_at, delivered_at,
                           status_name, latest_stage_name
                    FROM shipments
                    WHERE car_no = ?
                    ORDER BY ticketed_at DESC
                    LIMIT 1
                    """,
                    (wagon.car_no,),
                ).fetchone()
                if row:
                    return self._row_to_snapshot(row)

            return None
        finally:
            conn.close()

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> ShipmentSnapshot:
        return ShipmentSnapshot(
            ydid=row["ydid"] or "",
            car_no=row["car_no"] or "",
            destination_name=row["destination_name"] or "",
            ticketed_at=row["ticketed_at"] or "",
            departed_at=row["departed_at"] or "",
            arrived_at=row["arrived_at"] or "",
            delivered_at=row["delivered_at"] or "",
            status_name=row["status_name"] or "",
            latest_stage_name=row["latest_stage_name"] or "",
        )

    @staticmethod
    def _safe_col(row: sqlite3.Row, column: str) -> str:
        """Read a column safely — returns '' if column doesn't exist."""
        try:
            return row[column] or ""
        except (IndexError, KeyError):
            return ""

    def _apply_updates(
        self,
        updates: list[UpdatePlan],
        *,
        project_id: str = "",
        ship_name: str = "",
    ) -> None:
        """Write updates to sop_agent.db.

        Writes time fields (departed_at, arrived_at, delivered_at) and
        dispatch_status.  Also applies the default confirmed_received rule:
        if all wagons are delivered per 95306 and the SOP does not require
        manual confirmation, auto-sets confirmed_received_at on each wagon
        and on the release_batch.
        """
        conn = sqlite3.connect(str(self.sop_db_path))
        try:
            # Get existing columns to skip missing ones
            existing_cols = self._sop_wagon_columns()
            existing_rb_cols = self._release_batch_columns(conn)

            # Group time-field updates by db_id
            time_updates: dict[str, dict[str, str]] = {}
            dispatch_updates: dict[str, str] = {}
            delivered_wagon_ids: set[str] = set()
            last_delivered_at: str = ""

            for u in updates:
                if u.field in ("departed_at", "arrived_at", "delivered_at"):
                    if u.field in existing_cols:
                        time_updates.setdefault(u.db_id, {})[u.field] = u.new_value
                    if u.field == "delivered_at":
                        delivered_wagon_ids.add(u.db_id)
                        last_delivered_at = u.new_value
                elif u.field == "dispatch_status":
                    dispatch_updates[u.db_id] = u.new_value

            # ── Write time-field updates ────────────────────────────
            for db_id, fields in time_updates.items():
                sets = ", ".join(f"{k} = ?" for k in fields)
                vals = list(fields.values()) + [db_id]
                conn.execute(
                    f"UPDATE wagon_shipments SET {sets} WHERE id = ?", vals
                )

            # ── R39: Default confirmed_received rule ─────────────────
            # If ALL wagons have delivered_at updates, auto-set
            # confirmed_received_at on each wagon AND on the release_batch.
            total_wagons = len(time_updates)
            if (
                "confirmed_received_at" in existing_cols
                and delivered_wagon_ids
                and len(delivered_wagon_ids) >= total_wagons
                and total_wagons > 0
            ):
                for db_id in delivered_wagon_ids:
                    conn.execute(
                        "UPDATE wagon_shipments SET confirmed_received_at = ? "
                        "WHERE id = ? AND confirmed_received_at IS NULL",
                        (last_delivered_at, db_id),
                    )
                # Also update release_batches
                if "confirmed_received_at" in existing_rb_cols:
                    batch_ids = set()
                    for db_id in delivered_wagon_ids:
                        row = conn.execute(
                            "SELECT batch_id FROM wagon_shipments WHERE id = ?",
                            (db_id,),
                        ).fetchone()
                        if row:
                            batch_ids.add(row[0])
                    for batch_id in batch_ids:
                        conn.execute(
                            "UPDATE release_batches "
                            "SET confirmed_received_at = ? "
                            "WHERE id = ? AND confirmed_received_at IS NULL",
                            (last_delivered_at, batch_id),
                        )

            # ── Update dispatch_status on release_batches ──────────
            if dispatch_updates:
                batch_ids = set()
                for u in updates:
                    # Find batch_id from wagon
                    row = conn.execute(
                        "SELECT batch_id FROM wagon_shipments WHERE id = ?",
                        (u.db_id,),
                    ).fetchone()
                    if row:
                        batch_ids.add(row[0])

                final_status = "delivered"  # highest priority
                for batch_id in batch_ids:
                    conn.execute(
                        """UPDATE release_batches
                           SET dispatch_status = ?,
                               dispatch_status_updated_at = datetime('now')
                           WHERE id = ?""",
                        (final_status, batch_id),
                    )

            conn.commit()
        finally:
            conn.close()
