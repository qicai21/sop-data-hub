from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ops_hub.matching.release_match_spec import ReleaseBatchMatchSpec, build_match_spec, row_matches_spec


@dataclass(frozen=True)
class ShipmentLinkageResult:
    triggered: bool
    reason: str = ""
    release_batch_id: str = ""
    inspection_file: str = ""
    matched_inspection_rows: int = 0
    db_rows: int = 0
    linked_rows: int = 0
    first_car: str = ""
    first_ticketed_at: str = ""
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)


def link_release_batch_to_inspection(
    release_batch: Mapping[str, Any] | Any,
    inspection_json_path: str | Path,
    rail_db_path: str | Path,
    *,
    write: bool = False,
    window_minutes: int = 30,
) -> ShipmentLinkageResult:
    """Build and optionally persist shipment_release_batch_matches for one batch.

    This is the SOP-authorized formal linkage boundary: callers must decide that
    the project route is allowed before invoking it with write=True.
    """
    spec = build_match_spec(release_batch)
    inspection_path = Path(inspection_json_path)
    if not inspection_path.exists():
        return ShipmentLinkageResult(False, "inspection-json-missing", spec.release_batch_id, str(inspection_path))

    payload = json.loads(inspection_path.read_text(encoding="utf-8"))
    rows = _matching_loading_rows(payload, spec)
    if not rows:
        return ShipmentLinkageResult(False, "no-inspection-rows-matched-release-spec", spec.release_batch_id, inspection_path.name)

    first_car = _first_car_no(rows)
    if not first_car:
        return ShipmentLinkageResult(False, "missing-first-car", spec.release_batch_id, inspection_path.name)

    conn = sqlite3.connect(str(rail_db_path))
    conn.row_factory = sqlite3.Row
    try:
        first_shipment = _find_first_shipment(conn, first_car, spec)
        if first_shipment is None:
            return ShipmentLinkageResult(False, "first-car-not-found", spec.release_batch_id, inspection_path.name, len(rows), 0, 0, first_car)

        shipments = _query_window_shipments(conn, str(first_shipment["ticketed_at"]), spec, window_minutes)
        if not shipments:
            return ShipmentLinkageResult(
                False,
                "db-window-empty",
                spec.release_batch_id,
                inspection_path.name,
                len(rows),
                0,
                0,
                first_car,
                str(first_shipment["ticketed_at"]),
            )

        candidate_rows = _build_candidate_rows(spec, inspection_path.name, rows, shipments)
        if write:
            _ensure_match_table(conn)
            _write_candidate_rows(conn, candidate_rows)
            conn.commit()

        return ShipmentLinkageResult(
            True,
            "linked" if write else "candidate-built",
            spec.release_batch_id,
            inspection_path.name,
            len(rows),
            len(shipments),
            len(candidate_rows) if write else 0,
            first_car,
            str(first_shipment["ticketed_at"]),
            candidate_rows,
        )
    finally:
        conn.close()


def _matching_loading_rows(payload: Mapping[str, Any], spec: ReleaseBatchMatchSpec) -> list[dict[str, Any]]:
    source_rows = [dict(row) for row in (payload.get("rows") or []) if isinstance(row, Mapping)]
    loading_limit = _loading_row_limit(payload, len(source_rows))
    rows = source_rows[:loading_limit]
    return [row for row in rows if not row.get("defect") and row_matches_spec(row, spec)]


def _loading_row_limit(payload: Mapping[str, Any], fallback: int) -> int:
    footer = payload.get("footer") if isinstance(payload.get("footer"), Mapping) else {}
    raw = footer.get("zhuangche_jieshu") or footer.get("装车结束") or footer.get("loading_rows")
    try:
        value = int(raw)
        if value > 0:
            return min(value, fallback)
    except Exception:
        pass
    return fallback


def _first_car_no(rows: list[Mapping[str, Any]]) -> str:
    for row in rows:
        car = str(row.get("car_no") or "").strip()
        if car:
            return car
    return ""


def _find_first_shipment(conn: sqlite3.Connection, first_car: str, spec: ReleaseBatchMatchSpec) -> sqlite3.Row | None:
    destinations = list(spec.station_aliases or (spec.destination_station,))
    placeholders = ",".join("?" for _ in destinations)
    sql = f"""
        SELECT * FROM shipments
        WHERE car_no = ?
          AND destination_name IN ({placeholders})
        ORDER BY ticketed_at DESC
        LIMIT 1
    """
    return conn.execute(sql, (first_car, *destinations)).fetchone()


def _query_window_shipments(
    conn: sqlite3.Connection,
    ticketed_at: str,
    spec: ReleaseBatchMatchSpec,
    window_minutes: int,
) -> list[sqlite3.Row]:
    anchor = datetime.strptime(ticketed_at, "%Y-%m-%d %H:%M:%S")
    start = (anchor - timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    end = (anchor + timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    destinations = list(spec.station_aliases or (spec.destination_station,))
    dest_placeholders = ",".join("?" for _ in destinations)
    cargo_clauses = " OR ".join("cargo_name LIKE ?" for _ in spec.cargo_aliases)
    sql = f"""
        SELECT * FROM shipments
        WHERE destination_name IN ({dest_placeholders})
          AND ticketed_at BETWEEN ? AND ?
          AND ({cargo_clauses})
        ORDER BY ticketed_at, car_no
    """
    cargo_args = [f"%{alias}%" for alias in spec.cargo_aliases]
    return conn.execute(sql, (*destinations, start, end, *cargo_args)).fetchall()


def _build_candidate_rows(
    spec: ReleaseBatchMatchSpec,
    inspection_file: str,
    inspection_rows: list[dict[str, Any]],
    shipments: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    ocr_by_car = {str(row.get("car_no") or "").strip(): row for row in inspection_rows if row.get("car_no")}
    fallback_rows = list(inspection_rows)
    candidates: list[dict[str, Any]] = []
    for index, shipment in enumerate(shipments, start=1):
        shipment_car = str(shipment["car_no"] or "")
        ocr_row = ocr_by_car.get(shipment_car)
        if ocr_row is None and fallback_rows:
            ocr_row = fallback_rows[min(index - 1, len(fallback_rows) - 1)]
        inspection_row_no = int(ocr_row.get("seq") or ocr_row.get("global_index") or index) if ocr_row else index
        planned_weight = _float_or_none(shipment["marked_weight"])
        row = {
            "id": _row_id(spec.release_batch_id, str(shipment["ydid"])),
            "release_batch_id": spec.release_batch_id,
            "release_batch_sequence": spec.batch_sequence or "unknown",
            "release_batch_date": spec.batch_date,
            "inspection_file": inspection_file,
            "inspection_row": inspection_row_no,
            "inspection_car_no": str((ocr_row or {}).get("car_no") or ""),
            "inspection_car_type": str((ocr_row or {}).get("car_type") or ""),
            "shipment_ydid": str(shipment["ydid"]),
            "shipment_car_no": shipment_car,
            "shipment_car_model": shipment["car_model"],
            "planned_weight": planned_weight,
            "marked_weight": shipment["marked_weight"],
            "loaded_at": shipment["loaded_at"],
            "ticketed_at": shipment["ticketed_at"],
            "origin_name": shipment["origin_name"],
            "destination_name": shipment["destination_name"],
            "cargo_name": shipment["cargo_name"],
            "transport_mode_name": shipment["transport_mode_name"],
            "status_code": shipment["status_code"],
            "status_name": shipment["status_name"],
            "latest_stage_name": shipment["latest_stage_name"],
            "latest_event_time": shipment["latest_event_time"],
            "match_rule": f"release MatchSpec: station={spec.station_aliases}; cargo={spec.cargo_aliases}; ship={spec.ship_aliases}; ticketed_at ±30m; DB car number authoritative",
        }
        candidates.append(row)
    return candidates


def _write_candidate_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO shipment_release_batch_matches (
              id, release_batch_id, release_batch_sequence, release_batch_date,
              inspection_file, inspection_row, inspection_car_no, inspection_car_type,
              shipment_ydid, shipment_car_no, shipment_car_model, planned_weight,
              marked_weight, loaded_at, ticketed_at, origin_name, destination_name, cargo_name,
              transport_mode_name, status_code, status_name, latest_stage_name,
              latest_event_time, match_rule
            ) VALUES (
              :id, :release_batch_id, :release_batch_sequence, :release_batch_date,
              :inspection_file, :inspection_row, :inspection_car_no, :inspection_car_type,
              :shipment_ydid, :shipment_car_no, :shipment_car_model, :planned_weight,
              :marked_weight, :loaded_at, :ticketed_at, :origin_name, :destination_name, :cargo_name,
              :transport_mode_name, :status_code, :status_name, :latest_stage_name,
              :latest_event_time, :match_rule
            )
            ON CONFLICT(release_batch_id, shipment_ydid) DO UPDATE SET
              release_batch_sequence = excluded.release_batch_sequence,
              release_batch_date = excluded.release_batch_date,
              inspection_file = excluded.inspection_file,
              inspection_row = excluded.inspection_row,
              inspection_car_no = excluded.inspection_car_no,
              inspection_car_type = excluded.inspection_car_type,
              shipment_car_no = excluded.shipment_car_no,
              shipment_car_model = excluded.shipment_car_model,
              planned_weight = excluded.planned_weight,
              marked_weight = excluded.marked_weight,
              loaded_at = excluded.loaded_at,
              ticketed_at = excluded.ticketed_at,
              origin_name = excluded.origin_name,
              destination_name = excluded.destination_name,
              cargo_name = excluded.cargo_name,
              transport_mode_name = excluded.transport_mode_name,
              status_code = excluded.status_code,
              status_name = excluded.status_name,
              latest_stage_name = excluded.latest_stage_name,
              latest_event_time = excluded.latest_event_time,
              match_rule = excluded.match_rule,
              updated_at = CURRENT_TIMESTAMP
            """,
            row,
        )


def _ensure_match_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
          id TEXT PRIMARY KEY,
          release_batch_id TEXT NOT NULL,
          release_batch_sequence TEXT NOT NULL,
          release_batch_date TEXT,
          inspection_file TEXT NOT NULL,
          inspection_row INTEGER NOT NULL,
          inspection_car_no TEXT,
          inspection_car_type TEXT,
          shipment_ydid TEXT NOT NULL,
          shipment_car_no TEXT NOT NULL,
          shipment_car_model TEXT,
          planned_weight REAL,
          marked_weight TEXT,
          loaded_at TEXT,
          ticketed_at TEXT,
          origin_name TEXT,
          destination_name TEXT,
          cargo_name TEXT,
          transport_mode_name TEXT,
          status_code TEXT,
          status_name TEXT,
          latest_stage_name TEXT,
          latest_event_time TEXT,
          match_rule TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(release_batch_id, shipment_ydid)
        )
        """
    )
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(shipment_release_batch_matches)").fetchall()
    }
    if "ticketed_at" not in columns:
        conn.execute("ALTER TABLE shipment_release_batch_matches ADD COLUMN ticketed_at TEXT")


def _row_id(release_batch_id: str, shipment_ydid: str) -> str:
    return hashlib.sha1(f"{release_batch_id}|{shipment_ydid}".encode("utf-8")).hexdigest()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None
