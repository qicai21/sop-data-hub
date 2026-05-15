from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Literal, Mapping

from ops_hub.matching.release_match_spec import build_match_spec, row_matches_spec
from ops_hub.matching.shipment_linkage import _ensure_match_table, _row_id, _write_candidate_rows

RunMode = Literal["plan", "commit"]


@dataclass(frozen=True)
class CandidateReconcileSummary:
    candidate_id: str
    source_file_name: str
    target_rows: int
    matched_rows: int
    planned_weight: float
    first_car: str = ""
    ticket_window_start: str = ""
    ticket_window_end: str = ""


@dataclass(frozen=True)
class InspectionReconcileResult:
    run_mode: RunMode
    release_batch_id: str
    project_id: str
    operator_note: str = ""
    safe_to_commit: bool = False
    requires_manual_review: bool = True
    review_reasons: list[str] = field(default_factory=list)
    reason: str = ""
    planned_write_count: int = 0
    planned_weight: float = 0.0
    matched_95306_count: int = 0
    duplicate_count: int = 0
    committed_count: int = 0
    distinct_shipment_ydid: int = 0
    distinct_wagon_no: int = 0
    formal_weight: float = 0.0
    actual_wagon_count: int | None = None
    candidates: list[CandidateReconcileSummary] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "run_mode": self.run_mode,
            "release_batch_id": self.release_batch_id,
            "project_id": self.project_id,
            "operator_note": self.operator_note,
            "safe_to_commit": self.safe_to_commit,
            "requires_manual_review": self.requires_manual_review,
            "review_reasons": list(self.review_reasons),
            "reason": self.reason,
            "planned_write_count": self.planned_write_count,
            "planned_weight": round(self.planned_weight, 3),
            "matched_95306_count": self.matched_95306_count,
            "duplicate_count": self.duplicate_count,
            "committed_count": self.committed_count,
            "distinct_shipment_ydid": self.distinct_shipment_ydid,
            "distinct_wagon_no": self.distinct_wagon_no,
            "formal_weight": round(self.formal_weight, 3),
            "actual_wagon_count": self.actual_wagon_count,
            "candidates": [summary.__dict__ for summary in self.candidates],
            "excluded": self.excluded,
            "reconcile_plan": {
                "planned_write_count": self.planned_write_count,
                "planned_weight": round(self.planned_weight, 3),
                "matched_95306_count": self.matched_95306_count,
                "excluded": self.excluded,
                "safe_to_commit": self.safe_to_commit,
                "requires_manual_review": self.requires_manual_review,
                "review_reasons": list(self.review_reasons),
            },
        }


def reconcile_inspection_shipments(
    *,
    business_db_path: str | Path,
    rail_db_path: str | Path,
    project_id: str,
    release_batch_id: str,
    run_mode: RunMode,
    operator_note: str,
    candidate_ids: Iterable[str] | None = None,
    inspection_json_paths: Iterable[str | Path] | None = None,
    window_minutes: int = 30,
) -> InspectionReconcileResult:
    """Reconcile SOP-authorized inspection candidates with 95306 shipments and build a shipment-entry plan.

    The business DB owns release_batches and inspection_ingestion_candidates.
    The 95306 DB owns shipments and shipment_release_batch_matches. This function
    is intentionally explicit and idempotent: plan never writes, commit writes
    only when the generated reconcile_plan is safe_to_commit, via
    ON CONFLICT(release_batch_id, shipment_ydid) upsert, and then recomputes
    release_batches.actual_wagon_count from the formal table.
    """
    if run_mode not in ("plan", "commit"):
        raise ValueError("run_mode must be plan or commit")

    biz = sqlite3.connect(str(business_db_path))
    biz.row_factory = sqlite3.Row
    rail = sqlite3.connect(str(rail_db_path))
    rail.row_factory = sqlite3.Row
    try:
        release = biz.execute("SELECT * FROM release_batches WHERE id = ?", (release_batch_id,)).fetchone()
        if release is None:
            return _failure(run_mode, release_batch_id, project_id, operator_note, "release-batch-not-found")
        if str(release["project"] or "") != project_id:
            return _failure(run_mode, release_batch_id, project_id, operator_note, "release-project-mismatch")

        candidates = _load_candidates(biz, release_batch_id, candidate_ids)
        path_candidates = _load_json_path_candidates(inspection_json_paths)
        if not candidates and not path_candidates:
            return _failure(run_mode, release_batch_id, project_id, operator_note, "no-candidates")

        spec = build_match_spec(dict(release))
        planned_rows: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        summaries: list[CandidateReconcileSummary] = []

        for candidate in candidates:
            candidate_status = str(candidate["status"] or "")
            if candidate_status not in {"candidate", "committed"}:
                excluded.append(_excluded(candidate["id"], candidate["source_file_name"], "", "candidate-status-not-candidate"))
                continue
            payload = json.loads(candidate["payload_json"])
            if not _candidate_authorized(payload):
                return _failure(run_mode, release_batch_id, project_id, operator_note, "candidate-sop-not-authorized")
            rows, row_excluded = _target_rows_for_release(payload, spec)
            excluded.extend(_with_candidate(candidate, row_excluded))
            candidate_rows, summary, shipment_excluded = _build_formal_rows_for_candidate(
                rail,
                spec,
                candidate_id=str(candidate["id"]),
                source_file_name=str(candidate["source_file_name"]),
                inspection_rows=rows,
                window_minutes=window_minutes,
            )
            planned_rows.extend(candidate_rows)
            summaries.append(summary)
            excluded.extend(shipment_excluded)

        for path_candidate in path_candidates:
            payload = json.loads(Path(path_candidate["path"]).read_text(encoding="utf-8"))
            if not _candidate_authorized(payload):
                return _failure(run_mode, release_batch_id, project_id, operator_note, "candidate-sop-not-authorized")
            rows, row_excluded = _target_rows_for_release(payload, spec)
            excluded.extend(_with_candidate(path_candidate, row_excluded))
            candidate_rows, summary, shipment_excluded = _build_formal_rows_for_candidate(
                rail,
                spec,
                candidate_id=str(path_candidate["id"]),
                source_file_name=str(path_candidate["source_file_name"]),
                inspection_rows=rows,
                window_minutes=window_minutes,
            )
            planned_rows.extend(candidate_rows)
            summaries.append(summary)
            excluded.extend(shipment_excluded)

        planned_rows, duplicate_count = _dedupe_planned_rows(planned_rows)
        planned_weight = sum(_float_or_zero(row.get("planned_weight")) for row in planned_rows)
        review_reasons = _review_reasons(planned_rows, excluded)
        safe_to_commit = not review_reasons
        requires_manual_review = not safe_to_commit
        reason = "ready-to-commit" if safe_to_commit else "requires-manual-review"

        committed_count = 0
        formal = _formal_summary(rail, release_batch_id)
        actual_wagon_count = formal["count"]
        if run_mode == "commit" and safe_to_commit:
            _ensure_match_table(rail)
            _write_candidate_rows(rail, planned_rows)
            rail.commit()
            formal = _formal_summary(rail, release_batch_id)
            committed_count = formal["count"]
            actual_wagon_count = _sync_release_batch_actuals(biz, release_batch_id, formal["count"])
            _mark_candidates_committed(biz, [str(candidate["id"]) for candidate in candidates], operator_note)

        return InspectionReconcileResult(
            run_mode=run_mode,
            release_batch_id=release_batch_id,
            project_id=project_id,
            operator_note=operator_note,
            safe_to_commit=safe_to_commit,
            requires_manual_review=requires_manual_review,
            review_reasons=review_reasons,
            reason=reason,
            planned_write_count=len(planned_rows),
            planned_weight=planned_weight,
            matched_95306_count=len(planned_rows),
            duplicate_count=duplicate_count,
            committed_count=committed_count,
            distinct_shipment_ydid=formal["distinct_ydid"],
            distinct_wagon_no=formal["distinct_wagon"],
            formal_weight=formal["weight"],
            actual_wagon_count=actual_wagon_count,
            candidates=summaries,
            excluded=excluded,
            rows=planned_rows,
        )
    finally:
        biz.close()
        rail.close()


def _failure(run_mode: RunMode, release_batch_id: str, project_id: str, operator_note: str, reason: str) -> InspectionReconcileResult:
    return InspectionReconcileResult(
        run_mode=run_mode,
        release_batch_id=release_batch_id,
        project_id=project_id,
        operator_note=operator_note,
        safe_to_commit=False,
        requires_manual_review=True,
        review_reasons=[reason],
        reason="requires-manual-review",
    )


def _load_candidates(biz: sqlite3.Connection, release_batch_id: str, candidate_ids: Iterable[str] | None) -> list[sqlite3.Row]:
    ids = [str(item) for item in candidate_ids or [] if str(item).strip()]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        return list(biz.execute(f"SELECT * FROM inspection_ingestion_candidates WHERE release_batch_id = ? AND id IN ({placeholders}) ORDER BY source_file_name", (release_batch_id, *ids)))
    return list(biz.execute("SELECT * FROM inspection_ingestion_candidates WHERE release_batch_id = ? ORDER BY source_file_name", (release_batch_id,)))


def _load_json_path_candidates(paths: Iterable[str | Path] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for index, path in enumerate(paths or [], start=1):
        p = Path(path)
        out.append({"id": f"json:{index}", "source_file_name": p.name, "path": str(p)})
    return out


def _candidate_authorized(payload: Mapping[str, Any]) -> bool:
    return payload.get("_agent_sop_authorized") is True or payload.get("sop_authorized") is True


def _target_rows_for_release(payload: Mapping[str, Any], spec: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_rows = [dict(row) for row in (payload.get("rows") or []) if isinstance(row, Mapping)]
    source_rows.sort(key=lambda r: int(r.get("seq") or r.get("global_index") or 0))
    if not source_rows:
        return [], []
    source_rows = [_normalize_footer_boundary_defect(row, payload) for row in source_rows]

    segment = _ship_segment_rows(source_rows, spec)
    if segment is None:
        loading_limit = _loading_row_limit(payload, len(source_rows))
        segment = [row for row in source_rows[:loading_limit] if not row.get("defect") and row_matches_spec(row, spec)]

    selected_ids = {_row_identity(row) for row in segment}
    excluded: list[dict[str, Any]] = []
    for row_for_reason in source_rows:
        if _row_identity(row_for_reason) not in selected_ids:
            excluded.append(
                {
                    "candidate_id": "",
                    "source_file_name": "",
                    "wagon_no": str(row_for_reason.get("car_no") or ""),
                    "inspection_row": row_for_reason.get("seq") or row_for_reason.get("global_index"),
                    "reason": _row_exclusion_reason(row_for_reason),
                }
            )
    return segment, excluded


def _normalize_footer_boundary_defect(row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Do not let a shifted OCR defect flag shrink the loaded footer range.

    If the footer says 装车N/排车M, a defect mark on row N is suspicious when
    there are trailing 排车 rows.  Keep row N active; the trailing rows after N
    remain defect/excluded.  This matches mixed manually split sheets where the
    loaded segment ends exactly at the footer loading count.
    """
    out = dict(row)
    footer = payload.get("footer") if isinstance(payload.get("footer"), Mapping) else {}
    try:
        loading_limit = int(footer.get("zhuangche_jieshu") or footer.get("装车结束") or 0)
        paiche_count = int(footer.get("paiche_jieshu") or footer.get("排车结束") or 0)
        seq = int(out.get("seq") or out.get("global_index") or 0)
    except Exception:
        return out
    if loading_limit > 0 and paiche_count > 0 and seq == loading_limit and out.get("defect"):
        manual = payload.get("_manual_assignment") if isinstance(payload.get("_manual_assignment"), Mapping) else {}
        max_seq = max(
            [int(r.get("seq") or r.get("global_index") or 0) for r in (payload.get("rows") or []) if isinstance(r, Mapping)]
            or [0]
        )
        try:
            row_count = int(payload.get("rows_count") or 0)
        except Exception:
            row_count = 0
        has_trailing_paiche_row = max_seq > loading_limit or row_count > loading_limit or int(manual.get("row_end") or 0) == loading_limit
        if has_trailing_paiche_row:
            out["defect"] = False
            out["defect_corrected_by_footer"] = True
    return out


def _row_exclusion_reason(row: Mapping[str, Any]) -> str:
    if row.get("defect"):
        return "ocr-defect"
    if re.search(r"\d{1,3}\s*节", _row_text(row)):
        return "section-count-marker"
    return "outside-target-release-segment"


def _ship_segment_rows(rows: list[dict[str, Any]], spec: Any) -> list[dict[str, Any]] | None:
    target_indexes = [i for i, row in enumerate(rows) if _row_mentions_any(row, spec.ship_aliases)]
    if not target_indexes:
        return None
    for target_index in target_indexes:
        count = _nearby_section_count(rows, target_index)
        if not count:
            continue
        start = _segment_start(rows, target_index, spec)
        end = min(len(rows), start + count)
        return rows[start:end]
    return None


def _row_mentions_any(row: Mapping[str, Any], aliases: Iterable[str]) -> bool:
    text = _row_text(row)
    return any(alias and alias in text for alias in aliases)


def _row_text(row: Mapping[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("cargo_info_raw", "cargo_info_effective", "remark", "ship_name"))


def _nearby_section_count(rows: list[dict[str, Any]], target_index: int) -> int | None:
    for row in rows[target_index : min(len(rows), target_index + 5)]:
        match = re.search(r"(\d{1,3})\s*节", _row_text(row))
        if match:
            return int(match.group(1))
    return None


def _segment_start(rows: list[dict[str, Any]], target_index: int, spec: Any) -> int:
    start = target_index
    for i in range(target_index, -1, -1):
        if row_matches_spec(rows[i], spec):
            start = i
        elif start != target_index:
            break
    return start


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


def _build_formal_rows_for_candidate(
    rail: sqlite3.Connection,
    spec: Any,
    *,
    candidate_id: str,
    source_file_name: str,
    inspection_rows: list[dict[str, Any]],
    window_minutes: int,
) -> tuple[list[dict[str, Any]], CandidateReconcileSummary, list[dict[str, Any]]]:
    excluded: list[dict[str, Any]] = []
    first_car = _first_car(inspection_rows)
    empty_summary = CandidateReconcileSummary(candidate_id, source_file_name, len(inspection_rows), 0, 0.0, first_car)
    if not first_car:
        excluded.append(_excluded(candidate_id, source_file_name, "", "missing-first-car"))
        return [], empty_summary, excluded
    first_shipment = _find_shipment_by_car(rail, first_car, spec)
    if first_shipment is None:
        excluded.append(_excluded(candidate_id, source_file_name, first_car, "first-car-not-found"))
        return [], empty_summary, excluded

    start, end = _ticket_window(str(first_shipment["ticketed_at"]), window_minutes)
    active_rows = [row for row in inspection_rows if not row.get("defect")]
    target_car_to_row = {str(row.get("car_no") or "").strip(): row for row in active_rows if row.get("car_no")}
    window_shipments = _query_all_shipments_in_window(rail, spec, start, end)
    active_row_count = len(active_rows)
    target_row_count = len(inspection_rows)
    use_db_authoritative_window = False
    if len(window_shipments) == active_row_count:
        use_db_authoritative_window = True
    elif target_row_count > active_row_count and len(window_shipments) == target_row_count:
        # Some live inspection slips carry OCR defect/no-match flags inside the
        # footer-confirmed loaded segment.  When the 95306 bucket cardinality
        # equals the selected target segment exactly, trust the DB window and
        # keep row-order traceability instead of dropping a true loaded wagon.
        use_db_authoritative_window = True
    else:
        excluded.append(
            {
                "candidate_id": candidate_id,
                "source_file_name": source_file_name,
                "wagon_no": first_car,
                "inspection_row": None,
                "reason": "95306-window-count-mismatch",
                "inspection_row_count": active_row_count,
                "raw_inspection_row_count": len(inspection_rows),
                "defect_row_count": len(inspection_rows) - active_row_count,
                "window_shipment_count": len(window_shipments),
            }
        )

    formal_rows: list[dict[str, Any]] = []
    # Defect / 排车 rows are normally removed before any lot-level statistics or formal
    # linkage. If the 95306 time-window cardinality exactly matches the active
    # (non-defect) inspection section count, trust the DB car list for that
    # section. If the DB cardinality instead matches the full selected target
    # segment, the footer-confirmed segment is authoritative and DB row order is
    # used to correct OCR defect/car-number noise inside that segment.
    if use_db_authoritative_window:
        fallback_rows = list(active_rows if len(window_shipments) == active_row_count else inspection_rows)
        for index, shipment in enumerate(window_shipments, start=1):
            car = str(shipment["car_no"] or "")
            ocr_row = target_car_to_row.get(car)
            if ocr_row is None and fallback_rows:
                ocr_row = fallback_rows[min(index - 1, len(fallback_rows) - 1)]
            formal_rows.append(_formal_row_from_shipment(spec, candidate_id, source_file_name, ocr_row or {"seq": index, "car_no": car}, shipment))
    else:
        shipment_by_car = {str(row["car_no"]): row for row in _query_shipments_for_cars_in_window(rail, target_car_to_row.keys(), spec, start, end)}
        for car, ocr_row in target_car_to_row.items():
            shipment = shipment_by_car.get(car)
            if shipment is None:
                excluded.append(_excluded(candidate_id, source_file_name, car, "no-matching-95306-window", ocr_row))
                continue
            formal_rows.append(_formal_row_from_shipment(spec, candidate_id, source_file_name, ocr_row, shipment))

    planned_weight = sum(_float_or_zero(row.get("planned_weight")) for row in formal_rows)
    summary = CandidateReconcileSummary(candidate_id, source_file_name, len(inspection_rows), len(formal_rows), planned_weight, first_car, start, end)
    return formal_rows, summary, excluded


def _first_car(rows: list[Mapping[str, Any]]) -> str:
    for row in rows:
        car = str(row.get("car_no") or "").strip()
        if car:
            return car
    return ""


def _find_shipment_by_car(conn: sqlite3.Connection, car_no: str, spec: Any) -> sqlite3.Row | None:
    destinations = list(spec.station_aliases or (spec.destination_station,))
    placeholders = ",".join("?" for _ in destinations)
    cargo_clause = " OR ".join("cargo_name LIKE ?" for _ in spec.cargo_aliases)
    return conn.execute(
        f"""
        SELECT * FROM shipments
        WHERE car_no = ?
          AND destination_name IN ({placeholders})
          AND ({cargo_clause})
        ORDER BY ticketed_at DESC
        LIMIT 1
        """,
        (car_no, *destinations, *[f"%{alias}%" for alias in spec.cargo_aliases]),
    ).fetchone()


def _ticket_window(ticketed_at: str, minutes: int) -> tuple[str, str]:
    anchor = datetime.strptime(ticketed_at, "%Y-%m-%d %H:%M:%S")
    return (
        (anchor - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),
        (anchor + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _query_all_shipments_in_window(conn: sqlite3.Connection, spec: Any, start: str, end: str) -> list[sqlite3.Row]:
    destinations = list(spec.station_aliases or (spec.destination_station,))
    dest_placeholders = ",".join("?" for _ in destinations)
    cargo_clause = " OR ".join("cargo_name LIKE ?" for _ in spec.cargo_aliases)
    return list(
        conn.execute(
            f"""
            SELECT * FROM shipments
            WHERE destination_name IN ({dest_placeholders})
              AND ticketed_at BETWEEN ? AND ?
              AND ({cargo_clause})
            ORDER BY ticketed_at, car_no
            """,
            (*destinations, start, end, *[f"%{alias}%" for alias in spec.cargo_aliases]),
        )
    )


def _query_shipments_for_cars_in_window(conn: sqlite3.Connection, cars: Iterable[str], spec: Any, start: str, end: str) -> list[sqlite3.Row]:
    car_list = [str(car) for car in cars if str(car).strip()]
    if not car_list:
        return []
    car_placeholders = ",".join("?" for _ in car_list)
    destinations = list(spec.station_aliases or (spec.destination_station,))
    dest_placeholders = ",".join("?" for _ in destinations)
    cargo_clause = " OR ".join("cargo_name LIKE ?" for _ in spec.cargo_aliases)
    return list(
        conn.execute(
            f"""
            SELECT * FROM shipments
            WHERE car_no IN ({car_placeholders})
              AND destination_name IN ({dest_placeholders})
              AND ticketed_at BETWEEN ? AND ?
              AND ({cargo_clause})
            ORDER BY ticketed_at, car_no
            """,
            (*car_list, *destinations, start, end, *[f"%{alias}%" for alias in spec.cargo_aliases]),
        )
    )


def _formal_row_from_shipment(spec: Any, candidate_id: str, source_file_name: str, ocr_row: Mapping[str, Any], shipment: sqlite3.Row) -> dict[str, Any]:
    shipment_ydid = str(shipment["ydid"])
    return {
        "id": _row_id(spec.release_batch_id, shipment_ydid),
        "release_batch_id": spec.release_batch_id,
        "release_batch_sequence": spec.batch_sequence or "unknown",
        "release_batch_date": spec.batch_date,
        "inspection_file": source_file_name,
        "inspection_row": int(ocr_row.get("seq") or ocr_row.get("global_index") or 0),
        "inspection_car_no": str(ocr_row.get("car_no") or ""),
        "inspection_car_type": str(ocr_row.get("car_type") or ""),
        "shipment_ydid": shipment_ydid,
        "shipment_car_no": str(shipment["car_no"] or ""),
        "shipment_car_model": shipment["car_model"],
        "planned_weight": _float_or_none(shipment["marked_weight"]),
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
        "match_rule": f"reconcile-inspection candidate={candidate_id}; release MatchSpec station={spec.station_aliases}; cargo={spec.cargo_aliases}; ticketed_at ±30m; OCR segment split; DB car number authoritative",
    }


def _dedupe_planned_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        key = (str(row["release_batch_id"]), str(row["shipment_ydid"]))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        out.append(row)
    return out, duplicates


def _review_reasons(planned_rows: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if not planned_rows:
        reasons.append("no-planned-rows")
    visible_other_segment = any(str(item.get("reason") or "") == "outside-target-release-segment" for item in excluded)
    unsafe_excluded_reasons = {
        "missing-first-car",
        "first-car-not-found",
        "db-window-empty",
        "no-matching-95306-window",
        "95306-window-count-mismatch",
    }
    for item in excluded:
        reason = str(item.get("reason") or "")
        if reason == "95306-window-count-mismatch" and visible_other_segment:
            continue
        if reason in unsafe_excluded_reasons and reason not in reasons:
            reasons.append(reason)
    return reasons


def _formal_summary(conn: sqlite3.Connection, release_batch_id: str) -> dict[str, Any]:
    _ensure_match_table(conn)
    row = conn.execute(
        """
        SELECT count(*) AS count,
               count(DISTINCT shipment_ydid) AS distinct_ydid,
               count(DISTINCT shipment_car_no) AS distinct_wagon,
               coalesce(sum(planned_weight), 0) AS weight
        FROM shipment_release_batch_matches
        WHERE release_batch_id = ?
        """,
        (release_batch_id,),
    ).fetchone()
    return {"count": int(row["count"] or 0), "distinct_ydid": int(row["distinct_ydid"] or 0), "distinct_wagon": int(row["distinct_wagon"] or 0), "weight": float(row["weight"] or 0)}


def _sync_release_batch_actuals(conn: sqlite3.Connection, release_batch_id: str, actual_wagon_count: int) -> int:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(release_batches)").fetchall()}
    if "actual_wagon_count" in columns:
        conn.execute("UPDATE release_batches SET actual_wagon_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (actual_wagon_count, release_batch_id))
        conn.commit()
    return actual_wagon_count


def _mark_candidates_committed(conn: sqlite3.Connection, candidate_ids: list[str], operator_note: str) -> None:
    ids = [candidate_id for candidate_id in candidate_ids if candidate_id]
    if not ids:
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(inspection_ingestion_candidates)").fetchall()}
    if {"status", "reason", "updated_at"}.issubset(columns):
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""
            UPDATE inspection_ingestion_candidates
            SET status = 'committed',
                reason = 'formal_95306_linkage_committed',
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            ids,
        )
    if _table_exists(conn, "image_ingestion_audit"):
        audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(image_ingestion_audit)").fetchall()}
        if {"status", "reason", "db_action", "requires_manual_review"}.issubset(audit_columns):
            for candidate_id in ids:
                conn.execute(
                    """
                    UPDATE image_ingestion_audit
                    SET status = 'ingested',
                        reason = 'formal_95306_linkage_committed',
                        db_action = 'formal_linkage_committed',
                        requires_manual_review = 0
                    WHERE db_record_ids LIKE ?
                      AND classified_category = '检装车通知单'
                    """,
                    (f"%{candidate_id}%",),
                )
    conn.commit()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None


def _excluded(candidate_id: str, source_file_name: str, wagon_no: str, reason: str, row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "source_file_name": source_file_name,
        "wagon_no": wagon_no,
        "inspection_row": (row or {}).get("seq") or (row or {}).get("global_index"),
        "reason": reason,
    }


def _with_candidate(candidate: Mapping[str, Any] | sqlite3.Row, excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in excluded:
        updated = dict(item)
        updated["candidate_id"] = str(candidate["id"])
        updated["source_file_name"] = str(candidate["source_file_name"])
        out.append(updated)
    return out


def _row_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("seq") or row.get("global_index") or ""), str(row.get("car_no") or ""))


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def _float_or_zero(value: Any) -> float:
    value = _float_or_none(value)
    return float(value or 0.0)
