"""Jiushan soybean internal departure-text reconciliation.

This turns internal group messages such as "八道 50节 新台子 大豆 诚信"
into the ydid -> ship ledger used by sync_jiusan_bulk_wagons.py.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sop_hub.sop.departure_text_parser import (
    canonicalize_loading_line,
    parse_departure_text,
)
from sop_hub.sop.lifecycle_transition import advance_lifecycle
from sop_hub.utils.time import now_iso_beijing


REPO_ROOT = Path(__file__).resolve().parents[3]
SOP_DB = REPO_ROOT / "data" / "sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
PROJECT = "jiusan"
DESTINATION = "新台子"
CONSIGNEE = "九三集团铁岭大豆科技有限公司"


@dataclass(frozen=True)
class ShipCountSegment:
    ship_name: str
    car_count: int


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _parse_msg_date(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return now_iso_beijing()[:10]
    # message_inbox stores "YYYY-MM-DD HH:MM:SS"; other callers may pass ISO.
    return raw[:10]


def _ticket_window(value: str | None) -> tuple[str, str]:
    raw = (value or "").strip()
    try:
        center = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        center = datetime.fromisoformat(_parse_msg_date(raw))
    start = center - timedelta(hours=8)
    end = center + timedelta(hours=8)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _jiusan_lot02_ships(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r["ship_name"])
        for r in conn.execute(
            "SELECT ship_name FROM release_batches "
            "WHERE project=? AND batch_sequence='lot02' AND ship_name IS NOT NULL",
            (PROJECT,),
        )
        if r["ship_name"]
    }


def parse_jiusan_ship_segments(text: str, known_ships: set[str], total_count: int) -> list[ShipCountSegment]:
    """Parse ship/count segments from a Jiushan departure text.

    Single-ship text inherits the parser-level total count. Mixed text only
    records ship/count pairs; it does not imply a car-level split order.
    """
    present = [s for s in sorted(known_ships, key=len, reverse=True) if s and s in text]
    if not present:
        return []
    if len(present) == 1:
        return [ShipCountSegment(present[0], total_count)] if total_count > 0 else []

    found: dict[str, int] = {}
    for ship in present:
        escaped = re.escape(ship)
        patterns = (
            rf"{escaped}\s*(\d{{1,3}})\s*(?:车|节)",
            rf"(\d{{1,3}})\s*(?:车|节)\s*{escaped}",
        )
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                found[ship] = int(m.group(1))
                break

    return [ShipCountSegment(ship, found[ship]) for ship in present if ship in found]


def fetch_jiusan_bulk_tickets_for_date(
    message_time: str,
    *,
    rail_db: str | Path = RAIL_DB,
) -> list[dict[str, Any]]:
    start, end = _ticket_window(message_time)
    conn = sqlite3.connect(str(rail_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ydid, czydid, car_no, car_model, cargo_name, shipper_name,
                   consignee_name, origin_name, destination_name,
                   accepted_at, loaded_at, ticketed_at, departed_at, arrived_at,
                   delivered_at, status_name, latest_stage_key, latest_stage_name,
                   latest_event_time, marked_weight, freight_fee,
                   transport_mode_code, transport_mode_name
            FROM shipments
            WHERE cargo_name LIKE '%豆%'
              AND origin_name = '高桥镇'
              AND destination_name = '新台子'
              AND transport_mode_name LIKE '%整车%'
              AND ticketed_at >= ?
              AND ticketed_at < ?
              AND car_no IS NOT NULL AND car_no != ''
            ORDER BY ticketed_at, car_no, ydid
            """,
            (start, end),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _existing_conflicts(
    conn: sqlite3.Connection,
    assignments: dict[str, str],
) -> list[dict[str, str]]:
    if not assignments:
        return []
    placeholders = ",".join("?" for _ in assignments)
    rows = conn.execute(
        f"SELECT ydid, car_no, ship_name, source_ref FROM bulk_loading_notice_wagon "
        f"WHERE ydid IN ({placeholders})",
        tuple(assignments.keys()),
    ).fetchall()
    conflicts = []
    for r in rows:
        expected = assignments.get(r["ydid"])
        if expected and r["ship_name"] and r["ship_name"] != expected:
            conflicts.append({
                "ydid": r["ydid"],
                "car_no": r["car_no"] or "",
                "existing_ship": r["ship_name"] or "",
                "expected_ship": expected,
                "source_ref": r["source_ref"] or "",
            })
    return conflicts


def _insert_notice_rows(
    conn: sqlite3.Connection,
    *,
    tickets: list[dict[str, Any]],
    ship_name: str,
    notice_date: str,
    track: str,
    source_ref: str,
) -> int:
    now = now_iso_beijing()
    inserted = 0
    total = len(tickets)
    for seq, t in enumerate(tickets, start=1):
        row_id = _stable_id(PROJECT, notice_date, track, str(seq), ship_name, t["ydid"])
        conn.execute(
            """
            INSERT OR REPLACE INTO bulk_loading_notice_wagon
            (id, project, notice_date, track, total_cars, car_seq, car_no, car_model,
             ship_name, lot, ydid, destination, consignee, source_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'lot02', ?, ?, ?, ?, ?)
            """,
            (
                row_id, PROJECT, notice_date, track, total, seq, t["car_no"],
                t.get("car_model") or "", ship_name, t["ydid"], DESTINATION,
                CONSIGNEE, source_ref, now,
            ),
        )
        inserted += 1
    return inserted


def _run_bulk_sync() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "scripts/sync_jiusan_bulk_wagons.py", "--apply"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    return {
        "returncode": proc.returncode,
        "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-18:]),
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-18:]),
    }


def _advance_ship_loading(
    conn: sqlite3.Connection,
    ship_name: str,
    *,
    db_path: str | Path,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, dispatch_status FROM release_batches "
        "WHERE project=? AND ship_name=? AND batch_sequence='lot02'",
        (PROJECT, ship_name),
    ).fetchone()
    if not row:
        return None
    if row["dispatch_status"] not in ("pending_freight", "enriched"):
        return {"action": "noop", "from_phase": row["dispatch_status"]}
    return advance_lifecycle(
        row["id"],
        "loading",
        reason="jiusan internal departure text reconciled",
        triggered_by="jiusan_departure_text_reconcile",
        db_path=db_path,
    )


def reconcile_jiusan_departure_text(
    input_json: dict[str, Any],
    *,
    db_path: str | Path = SOP_DB,
    rail_db: str | Path = RAIL_DB,
    run_sync: bool = True,
) -> dict[str, Any]:
    text = str(input_json.get("text_content") or "").strip()
    msg_id = str(input_json.get("message_id") or "")
    msg_time = str(input_json.get("received_datetime") or "")
    notice_date = _parse_msg_date(msg_time)
    candidate = parse_departure_text(
        text,
        group_id=str(input_json.get("group_name") or ""),
        message_id=msg_id,
        message_time=msg_time,
    )
    if candidate.status != "complete" or candidate.project_id != PROJECT:
        return {
            "action": "skipped",
            "status": "skipped",
            "output_json": {
                "reason": "not a complete jiusan departure text",
                "candidate": candidate.to_dict(),
            },
        }

    db = Path(db_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        known_ships = _jiusan_lot02_ships(conn)
        segments = parse_jiusan_ship_segments(text, known_ships, candidate.car_count)
        if not segments:
            return {
                "action": "manual_review_required",
                "status": "skipped",
                "output_json": {
                    "reason": "no known jiusan lot02 ship found in text",
                    "candidate": candidate.to_dict(),
                },
            }

        tickets = fetch_jiusan_bulk_tickets_for_date(msg_time or notice_date, rail_db=rail_db)
        ticket_summary = [
            {"ydid": t["ydid"], "car_no": t["car_no"], "ticketed_at": t.get("ticketed_at") or ""}
            for t in tickets
        ]
        total_segment_count = sum(s.car_count for s in segments)
        expected_count = total_segment_count if len(segments) > 1 else candidate.car_count
        if len(tickets) != expected_count or total_segment_count != expected_count:
            return {
                "action": "manual_review_required",
                "status": "skipped",
                "output_json": {
                    "reason": "95306 ticket count and text count do not match",
                    "text_count": expected_count,
                    "parser_count": candidate.car_count,
                    "segment_count": total_segment_count,
                    "rail_ticket_count": len(tickets),
                    "segments": [s.__dict__ for s in segments],
                    "tickets": ticket_summary,
                },
            }

        if len(segments) != 1:
            return {
                "action": "manual_review_required",
                "status": "skipped",
                "output_json": {
                    "reason": "mixed ships require car-level split; refuse to infer from 95306 ticket order",
                    "segments": [s.__dict__ for s in segments],
                    "tickets": ticket_summary,
                },
            }

        ship = segments[0].ship_name
        assignments = {t["ydid"]: ship for t in tickets}
        conflicts = _existing_conflicts(conn, assignments)
        if conflicts:
            return {
                "action": "manual_review_required",
                "status": "skipped",
                "output_json": {
                    "reason": "existing bulk_loading_notice_wagon conflicts with text assignment",
                    "ship_name": ship,
                    "conflicts": conflicts,
                    "tickets": ticket_summary,
                },
            }

        source_ref = (
            f"{input_json.get('group_name') or '铁晟大豆业务内部沟通群'}"
            f"[{msg_id}]/文本发车:{text}"
        )
        inserted = _insert_notice_rows(
            conn,
            tickets=tickets,
            ship_name=ship,
            notice_date=notice_date,
            track=canonicalize_loading_line(candidate.lane_or_track),
            source_ref=source_ref,
        )
        conn.commit()
    finally:
        conn.close()

    sync_result = _run_bulk_sync() if run_sync else {"skipped": True}
    lifecycle_result = None
    conn2 = sqlite3.connect(str(db))
    conn2.row_factory = sqlite3.Row
    try:
        lifecycle_result = _advance_ship_loading(conn2, ship, db_path=db)
    finally:
        conn2.close()

    status = "succeeded" if not run_sync or sync_result.get("returncode") == 0 else "failed"
    return {
        "action": "succeeded" if status == "succeeded" else "failed",
        "status": status,
        "output_json": {
            "notice_rows_inserted": inserted,
            "ship_name": ship,
            "notice_date": notice_date,
            "track": canonicalize_loading_line(candidate.lane_or_track),
            "rail_ticket_count": len(tickets),
            "sync": sync_result,
            "lifecycle": lifecycle_result,
        },
        "error_message": None if status == "succeeded" else sync_result.get("stderr_tail"),
    }
