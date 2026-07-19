"""Sync 95306 shipment status snapshots into sop_agent.db shipment fact tables.

Reads wagon_shipments and/or wagon_container_shipments for a project + ship_name,
queries 95306_collection.sqlite3 for latest status, and writes time fields plus
status_name / latest_stage_key back.

Matching priority: ydid → car_no + destination_name → car_no.
Status mapping: 已发车→dispatched, 到站→arrived, 交付/确认收货/已卸车→delivered.

This module:
- reads sop_agent.db (wagon_* + release_batches)
- reads 95306_collection.sqlite3 (shipments table, read-only)
- writes sop_agent.db ONLY with --apply / dry_run=False
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

STATUS_NAME_TO_STAGE_KEY: dict[str, str] = {
    "交付": "delivered",
    "货物已交付": "delivered",
    "已交付": "delivered",
    "确认收货": "delivered",
    "已卸车": "unloading_completed",
    "到站": "arrived",
    "已到站": "arrived",
    "发车": "departed",
    "已发车": "departed",
    "已制单": "ticketed",
    "制票": "ticketed",
}

_TABLE_WAGON = "wagon_shipments"
_TABLE_CONTAINER = "wagon_container_shipments"

# R39: Default rule — if SOP does not declare additional manual confirmation,
# 95306 "交付" status implies confirmed_received.
# This can be overridden by SOP YAML in the future.
DEFAULT_CONFIRMED_RECEIVED_RULE = (
    "If all wagons are delivered per 95306 and SOP does not require "
    "manual confirmation, auto-set confirmed_received_at = delivered_at."
)


@dataclass
class WagonRow:
    """A wagon_shipments or wagon_container_shipments row to sync."""
    db_id: str
    batch_id: str
    car_no: str
    ydid: str = ""
    destination_name: str = ""
    departed_at: str = ""
    arrived_at: str = ""
    delivered_at: str = ""
    status_name: str = ""
    latest_stage_key: str = ""
    dispatch_status: str = ""
    source_table: str = _TABLE_WAGON


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
    """A planned update for one shipment fact row."""
    db_id: str
    car_no: str
    field: str
    old_value: str
    new_value: str
    source: str  # "95306"
    reason: str = ""
    source_table: str = _TABLE_WAGON


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

        # ── 1. Load shipment fact rows (wagon + container) ───────────
        wagons = self._load_units(project_id=project_id, ship_name=ship_name)
        result.total_wagons = len(wagons)
        if not wagons:
            return result

        # ── 2. Check schema for tables we actually load ─────────────
        if any(w.source_table == _TABLE_WAGON for w in wagons):
            sop_cols = self._table_columns(_TABLE_WAGON)
            for col in ("departed_at", "arrived_at", "delivered_at"):
                if col not in sop_cols:
                    result.schema_missing_fields.append(col)

        # ── 3. Query 95306 for each unit ─────────────────────────────
        updates: list[UpdatePlan] = []
        unmatched: list[str] = []
        matched_ids: set[str] = set()

        for wagon in wagons:
            snapshot = self._find_snapshot(wagon)
            if snapshot is None:
                unmatched.append(wagon.ydid or wagon.car_no or wagon.db_id)
                continue

            matched_ids.add(wagon.db_id)
            stage = snapshot.latest_stage_name or snapshot.status_name or ""
            fields_to_update = STATUS_TO_FIELDS.get(stage, [])
            # Also try status_name key if stage label alone missed
            if not fields_to_update and snapshot.status_name:
                fields_to_update = STATUS_TO_FIELDS.get(snapshot.status_name, [])

            for field in fields_to_update:
                new_value = getattr(snapshot, field, "") or ""
                old_value = getattr(wagon, field, "") or ""
                if not new_value:
                    continue
                # Don't overwrite existing non-NULL time values
                if old_value and str(old_value).strip():
                    continue
                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                    source="95306",
                    reason=f"95306 status={stage}",
                    source_table=wagon.source_table,
                ))
                if field == "departed_at":
                    result.departed_update_count += 1
                elif field == "arrived_at":
                    result.arrived_update_count += 1
                elif field == "delivered_at":
                    result.delivered_update_count += 1

            # status_name / latest_stage_key: always refresh from 95306 when present
            snap_status = snapshot.status_name or stage
            if snap_status and snap_status != (wagon.status_name or ""):
                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field="status_name",
                    old_value=wagon.status_name or "",
                    new_value=snap_status,
                    source="95306",
                    reason=f"95306 status_name={snap_status}",
                    source_table=wagon.source_table,
                ))
            stage_key = STATUS_NAME_TO_STAGE_KEY.get(stage) or STATUS_NAME_TO_STAGE_KEY.get(
                snapshot.status_name or "", ""
            )
            if stage_key and stage_key != (wagon.latest_stage_key or ""):
                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field="latest_stage_key",
                    old_value=wagon.latest_stage_key or "",
                    new_value=stage_key,
                    source="95306",
                    reason=f"95306 stage→{stage_key}",
                    source_table=wagon.source_table,
                ))

            new_dispatch = STAGE_TO_DISPATCH_STATUS.get(stage, "") or STAGE_TO_DISPATCH_STATUS.get(
                snapshot.status_name or "", ""
            )
            if new_dispatch and new_dispatch != wagon.dispatch_status:
                updates.append(UpdatePlan(
                    db_id=wagon.db_id,
                    car_no=wagon.car_no,
                    field="dispatch_status",
                    old_value=wagon.dispatch_status,
                    new_value=new_dispatch,
                    source="95306",
                    reason=f"95306 stage={stage}",
                    source_table=wagon.source_table,
                ))

        result.updates = updates
        result.unmatched_wagons = unmatched
        result.update_count = len(updates)
        result.matched_count = len(matched_ids)
        result.unmatched_count = result.total_wagons - result.matched_count

        # ── 4. Batch-level suggestion ─────────────────────────────────
        delivered_units = sum(1 for u in updates if u.field == "delivered_at")
        stage_delivered = sum(
            1 for u in updates
            if u.field == "latest_stage_key" and u.new_value in ("delivered", "unloading_completed")
        )
        if delivered_units >= result.total_wagons or stage_delivered >= result.total_wagons:
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
            self._apply_updates(updates)

        return result

    # ── internal ──────────────────────────────────────────────────────

    def _load_units(
        self, *, project_id: str = "", ship_name: str = ""
    ) -> list[WagonRow]:
        """Load wagon and/or container fact rows joined with release_batches.

        Jilin uses container table as sole live fact source — skip wagon rows
        for that project so frozen car-level audit snapshots are not re-synced.
        """
        conn = sqlite3.connect(str(self.sop_db_path))
        conn.row_factory = sqlite3.Row
        try:
            aliases = self._project_aliases(project_id)
            units: list[WagonRow] = []

            load_wagons = True
            if project_id and "jilin_jingang" in project_id:
                load_wagons = False
            # If only containers exist for this ship, prefer them for any project
            has_container = self._table_exists(conn, _TABLE_CONTAINER)

            if load_wagons and self._table_exists(conn, _TABLE_WAGON):
                units.extend(
                    self._load_from_table(
                        conn, _TABLE_WAGON, ship_name=ship_name, aliases=aliases,
                    )
                )
            if has_container:
                units.extend(
                    self._load_from_table(
                        conn, _TABLE_CONTAINER, ship_name=ship_name, aliases=aliases,
                    )
                )
            # Auto-detect jilin-like: ship has only containers in open lots
            if load_wagons and has_container and not units:
                units.extend(
                    self._load_from_table(
                        conn, _TABLE_CONTAINER, ship_name=ship_name, aliases=aliases,
                    )
                )
            return units
        finally:
            conn.close()

    @staticmethod
    def _project_aliases(project_id: str) -> set[str] | None:
        if not project_id:
            return None
        aliases = {project_id}
        if "jilin_jingang" in project_id:
            aliases.add("吉林金钢-锦州港铁矿发运项目")
        return aliases

    def _load_from_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        *,
        ship_name: str,
        aliases: set[str] | None,
    ) -> list[WagonRow]:
        cols = self._table_columns_conn(conn, table)
        if "batch_id" not in cols or "id" not in cols:
            return []
        # Build SELECT with graceful missing columns
        def col(name: str, alias: str | None = None) -> str:
            a = alias or name
            return f"t.{name} AS {a}" if name in cols else f"'' AS {a}"

        select_sql = ", ".join([
            "t.id",
            "t.batch_id",
            col("car_no"),
            col("ydid"),
            col("destination_name"),
            col("departed_at"),
            col("arrived_at"),
            col("delivered_at"),
            col("status_name"),
            col("latest_stage_key"),
            "rb.dispatch_status",
            "rb.project",
        ])
        ship_pred = (
            "COALESCE(t.ship_name, rb.ship_name, '') = ?"
            if "ship_name" in cols
            else "COALESCE(rb.ship_name,'') = ?"
        )
        sql = (
            f"SELECT {select_sql} FROM {table} t "
            f"JOIN release_batches rb ON t.batch_id = rb.id "
            f"WHERE {ship_pred}"
        )
        rows = conn.execute(sql, (ship_name,)).fetchall()
        if aliases is not None:
            filtered = []
            for r in rows:
                proj = r["project"] or ""
                # fact.project_id when present is already not in SELECT; filter rb.project
                if proj in aliases:
                    filtered.append(r)
                    continue
                # jilin legacy: project field may already be snake id
            rows = filtered
        return [
            WagonRow(
                db_id=row["id"],
                batch_id=row["batch_id"],
                car_no=row["car_no"] or "",
                ydid=row["ydid"] or "",
                destination_name=row["destination_name"] or "",
                departed_at=row["departed_at"] or "",
                arrived_at=row["arrived_at"] or "",
                delivered_at=row["delivered_at"] or "",
                status_name=row["status_name"] or "",
                latest_stage_key=row["latest_stage_key"] or "",
                dispatch_status=row["dispatch_status"] or "",
                source_table=table,
            )
            for row in rows
        ]

    def _table_columns(self, table: str) -> set[str]:
        conn = sqlite3.connect(str(self.sop_db_path))
        try:
            return self._table_columns_conn(conn, table)
        finally:
            conn.close()

    @staticmethod
    def _table_columns_conn(conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            return set()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _sop_wagon_columns(self) -> set[str]:
        """Backward-compatible alias."""
        return self._table_columns(_TABLE_WAGON)

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

        Priority: ydid → car_no + destination_name → car_no.
        """
        conn = sqlite3.connect(str(self.rail_db_path))
        conn.row_factory = sqlite3.Row
        select_sql = """
            SELECT ydid, car_no, destination_name,
                   ticketed_at, departed_at, arrived_at, delivered_at,
                   status_name, latest_stage_name
            FROM shipments
        """
        try:
            if wagon.ydid:
                row = conn.execute(
                    select_sql + " WHERE ydid = ? LIMIT 1",
                    (wagon.ydid,),
                ).fetchone()
                if row:
                    return self._row_to_snapshot(row)

            if wagon.car_no and wagon.destination_name:
                row = conn.execute(
                    select_sql
                    + " WHERE car_no = ? AND destination_name = ? "
                    + "ORDER BY ticketed_at DESC LIMIT 1",
                    (wagon.car_no, wagon.destination_name),
                ).fetchone()
                if row:
                    return self._row_to_snapshot(row)

            if wagon.car_no:
                row = conn.execute(
                    select_sql
                    + " WHERE car_no = ? ORDER BY ticketed_at DESC LIMIT 1",
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

    def _apply_updates(self, updates: list[UpdatePlan]) -> None:
        """Write updates to sop_agent.db fact tables (wagon and/or container).

        Time fields only fill empties (planned upstream). status_name /
        latest_stage_key always refresh. Does not rewrite 95306 DB.
        """
        conn = sqlite3.connect(str(self.sop_db_path))
        try:
            existing_rb_cols = self._release_batch_columns(conn)
            # Group field updates by (table, id)
            by_row: dict[tuple[str, str], dict[str, str]] = {}
            id_to_table: dict[str, str] = {}
            delivered_ids: set[str] = set()
            last_delivered_at = ""

            for u in updates:
                if u.field == "dispatch_status":
                    # Batch-level only; resolve later
                    continue
                table = u.source_table or _TABLE_WAGON
                cols = self._table_columns_conn(conn, table)
                if u.field not in cols:
                    continue
                by_row.setdefault((table, u.db_id), {})[u.field] = u.new_value
                id_to_table[u.db_id] = table
                if u.field == "delivered_at":
                    delivered_ids.add(u.db_id)
                    last_delivered_at = u.new_value

            for (table, db_id), fields in by_row.items():
                sets = ", ".join(f"{k} = ?" for k in fields)
                if "updated_at" in self._table_columns_conn(conn, table):
                    sets += ", updated_at = CURRENT_TIMESTAMP"
                vals = list(fields.values()) + [db_id]
                conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", vals)

            # confirmed_received_at on wagon table only (legacy R39)
            for db_id in delivered_ids:
                table = id_to_table.get(db_id, _TABLE_WAGON)
                cols = self._table_columns_conn(conn, table)
                if "confirmed_received_at" in cols and last_delivered_at:
                    conn.execute(
                        f"UPDATE {table} SET confirmed_received_at = ? "
                        f"WHERE id = ? AND (confirmed_received_at IS NULL OR confirmed_received_at = '')",
                        (last_delivered_at, db_id),
                    )

            # Resolve batch_ids for any unit that got delivered_at or stage delivered
            batch_ids: set[str] = set()
            for u in updates:
                table = u.source_table or _TABLE_WAGON
                row = conn.execute(
                    f"SELECT batch_id FROM {table} WHERE id = ?", (u.db_id,)
                ).fetchone()
                if row:
                    batch_ids.add(row[0])

            # NOTE: do NOT force release_batches.dispatch_status='delivered' here.
            # Lifecycle closeout owns all_loaded → confirmed_received transitions.
            # Only stamp confirmed_received_at timestamp on batch when all units delivered.
            if (
                "confirmed_received_at" in existing_rb_cols
                and delivered_ids
                and last_delivered_at
            ):
                for batch_id in batch_ids:
                    conn.execute(
                        "UPDATE release_batches SET confirmed_received_at = ? "
                        "WHERE id = ? AND (confirmed_received_at IS NULL OR confirmed_received_at = '')",
                        (last_delivered_at, batch_id),
                    )

            conn.commit()
        finally:
            conn.close()
