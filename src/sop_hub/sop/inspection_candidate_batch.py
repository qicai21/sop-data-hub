"""Stable batch decisions for delayed inspection-candidate retries."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence


OPEN_BATCH_STATUSES = frozenset({"pending_freight", "enriched", "loading"})


def resolve_preassigned_open_batch(
    conn: sqlite3.Connection,
    candidate: Mapping[str, Any],
    *,
    project_id: str,
    ship_name: str,
    destination: str,
) -> str | None:
    """Keep a candidate's earlier batch assignment when it is still valid."""
    batch_id = str(candidate.get("release_batch_id") or "").strip()
    if not batch_id:
        return None
    row = conn.execute(
        "SELECT id, project, ship_name, destination_station, dispatch_status "
        "FROM release_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not row:
        return None

    values = dict(row) if hasattr(row, "keys") else {
        "id": row[0],
        "project": row[1],
        "ship_name": row[2],
        "destination_station": row[3],
        "dispatch_status": row[4],
    }
    if str(values.get("dispatch_status") or "") not in OPEN_BATCH_STATUSES:
        return None
    if str(values.get("project") or "") != project_id:
        return None
    if str(values.get("ship_name") or "") != ship_name:
        return None
    batch_destination = str(values.get("destination_station") or "").strip()
    if destination and batch_destination and batch_destination != destination:
        return None
    return str(values.get("id") or "") or None


def candidate_loading_cars_already_persisted(
    conn: sqlite3.Connection,
    candidate: Mapping[str, Any],
    loading_car_nos: Sequence[str],
) -> bool:
    """Return true when every loading car is already in the assigned batch."""
    batch_id = str(candidate.get("release_batch_id") or "").strip()
    cars = list(dict.fromkeys(str(car or "").strip() for car in loading_car_nos))
    cars = [car for car in cars if car]
    if not batch_id or not cars:
        return False
    placeholders = ",".join("?" for _ in cars)
    row = conn.execute(
        f"SELECT COUNT(DISTINCT car_no) FROM wagon_shipments "
        f"WHERE batch_id=? AND car_no IN ({placeholders})",
        [batch_id, *cars],
    ).fetchone()
    return int((row or [0])[0] or 0) == len(cars)
