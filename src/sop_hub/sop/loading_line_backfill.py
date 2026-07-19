"""Backfill loading lines in both SOP shipment ledgers from group text history.

This module is intentionally scoped to maintained tooling, not one-off SQL:

- reads `message_inbox` raw text from the shared workgroup
- extracts departure-like text segments, including mixed-train messages
- matches them against missing wagon/container shipment train groups
- optionally writes the canonical loading line back to the DB
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
import sqlite3
from typing import Iterable

import yaml

from sop_hub.sop.departure_text_parser import (
    _RE_CAR_COUNT,
    canonicalize_loading_line,
    parse_departure_text,
)


DESTINATION_ALIASES = {
    "四平": ("四平镍", "四平铁", "四平方向", "四平"),
    "朝阳西": ("朝阳西铁矿", "朝阳西铁", "朝阳西", "朝阳铁", "乌铁"),
    "汐子": ("汐子铁", "汐子", "沙子"),
    "新台子": ("新台子", "新台"),
}

DESTINATION_PROJECT = {
    "四平": "jilin_jingang_jinzhou",
    "朝阳西": "chaoyang_steel",
    "汐子": "zhongtang_special_steel",
    "新台子": "jiusan",
}

PROJECT_GROUPS = {
    "jiusan": "铁晟大豆业务内部沟通群",
    "jilin_jingang_jinzhou": "铁晟业务工作群",
    "chaoyang_steel": "铁晟业务工作群",
    "zhongtang_special_steel": "铁晟业务工作群",
}

_LEDGER_TABLES = ("wagon_shipments", "wagon_container_shipments")
_JIUSAN_FALLBACK_LINES = {
    "wagon_shipments": "七道",
    "wagon_container_shipments": "八道",
}

_LANE_RE = re.compile(r"(煤[一二三四五六七八九十](?:道)?|[0-9一二三四五六七八九十]+道)")


@dataclass(frozen=True)
class MissingTrainGroup:
    source_table: str
    project_id: str
    dispatch_train_code: str
    ship_name: str
    destination_name: str
    ticket_date: str
    ticket_time: str
    ticketed_at: str
    car_count: int


@dataclass(frozen=True)
class ParsedSegment:
    raw_text: str
    lane: str
    project_id: str
    destination_name: str
    ship_name: str
    car_count: int


@dataclass(frozen=True)
class CandidateMatch:
    group: MissingTrainGroup
    inbox_id: int
    received_datetime: str
    lane: str
    ship_name: str
    car_count: int
    text_preview: str
    score: int


@dataclass(frozen=True)
class BackfillDecision:
    group: MissingTrainGroup
    status: str
    lane: str = ""
    inbox_id: int | None = None
    received_datetime: str = ""
    text_preview: str = ""
    candidate_count: int = 0
    note: str = ""


def _db_path(db_path: str | Path | None = None) -> Path:
    if db_path:
        return Path(db_path)
    return Path(__file__).resolve().parents[3] / "data" / "sop_agent.db"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _load_known_ships_by_project() -> dict[str, set[str]]:
    config_dir = Path(__file__).resolve().parents[3] / "config" / "project_sops"
    mapping: dict[str, set[str]] = {}
    for yp in config_dir.glob("*.yaml"):
        raw = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        project_id = str(raw.get("project_id") or "").strip()
        if not project_id:
            project_id = str((raw.get("project_meta") or {}).get("project_id") or "").strip()
        if not project_id:
            continue
        ships = {
            str(s).strip()
            for s in ((raw.get("project_meta") or {}).get("known_ships") or [])
            if str(s).strip()
        }
        if ships:
            mapping.setdefault(project_id, set()).update(ships)
    return mapping


def _all_known_ships() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for project_id, ships in _load_known_ships_by_project().items():
        for ship in ships:
            pairs.append((ship, project_id))
    # Longer ship names first to avoid partial overlaps.
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def _extract_lane(raw_text: str) -> str:
    m = _LANE_RE.search(raw_text.replace(" ", ""))
    if not m:
        return ""
    token = m.group(1)
    if token.startswith("煤"):
        return token[:-1] if token.endswith("道") else token
    return canonicalize_loading_line(token)


def _extract_destination(raw_text: str) -> tuple[str, str]:
    text = raw_text.replace(" ", "")
    for canonical, aliases in DESTINATION_ALIASES.items():
        for alias in aliases:
            if alias in text:
                return canonical, DESTINATION_PROJECT.get(canonical, "")
    candidate = parse_departure_text(raw_text)
    return candidate.destination, candidate.project_id


def _find_ship_occurrences(raw_text: str) -> list[tuple[int, str, str]]:
    occurrences: list[tuple[int, str, str]] = []
    normalized = raw_text.replace("“", "").replace("”", "").replace('"', "")
    for ship, project_id in _all_known_ships():
        start = normalized.find(ship)
        while start >= 0:
            occurrences.append((start, ship, project_id))
            start = normalized.find(ship, start + 1)
    occurrences.sort(key=lambda item: item[0])
    return occurrences


def _last_count(text: str) -> int | None:
    matches = list(_RE_CAR_COUNT.finditer(text))
    if not matches:
        return None
    return int(matches[-1].group(1))


def _first_count(text: str) -> int | None:
    m = _RE_CAR_COUNT.search(text)
    if not m:
        return None
    return int(m.group(1))


def parse_workgroup_segments(raw_text: str) -> list[ParsedSegment]:
    text = (raw_text or "").strip()
    if not text:
        return []
    lane = _extract_lane(text)
    if not lane:
        return []
    destination, dest_project = _extract_destination(text)
    segments: list[ParsedSegment] = []
    occurrences = _find_ship_occurrences(text)

    if occurrences:
        for idx, (pos, ship, ship_project) in enumerate(occurrences):
            next_pos = occurrences[idx + 1][0] if idx + 1 < len(occurrences) else len(text)
            after = text[pos:next_pos]
            before = text[max(0, pos - 20):pos]
            count = _first_count(after)
            if count is None:
                count = _last_count(before)
            if count is None:
                continue
            project_id = dest_project or ship_project
            segments.append(
                ParsedSegment(
                    raw_text=text,
                    lane=lane,
                    project_id=project_id,
                    destination_name=destination,
                    ship_name=ship,
                    car_count=count,
                )
            )

    # Fallback: no ship found, but the whole message still may be enough.
    if not segments:
        candidate = parse_departure_text(text)
        if candidate.status == "complete" and candidate.car_count >= 0:
            segments.append(
                ParsedSegment(
                    raw_text=text,
                    lane=canonicalize_loading_line(candidate.lane_or_track or lane),
                    project_id=candidate.project_id or dest_project,
                    destination_name=candidate.destination or destination,
                    ship_name=candidate.optional_ship_name or "",
                    car_count=candidate.car_count,
                )
            )

    # Deduplicate exact same semantic segment inside one text.
    seen: set[tuple[str, str, str, int]] = set()
    deduped: list[ParsedSegment] = []
    for seg in segments:
        key = (seg.project_id, seg.ship_name, seg.lane, seg.car_count)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seg)
    return deduped


def load_missing_train_groups(
    conn: sqlite3.Connection,
    *,
    project_ids: Iterable[str],
    since: str = "",
) -> list[MissingTrainGroup]:
    project_ids = tuple(project_ids)
    if not project_ids:
        return []
    ph = ",".join("?" for _ in project_ids)
    groups: list[MissingTrainGroup] = []
    for table in _LEDGER_TABLES:
        columns = _table_columns(conn, table)
        if not {"project_id", "ticketed_at", "loading_line"} <= columns:
            continue
        where_since = " AND ticketed_at >= ?" if since else ""
        params = [*project_ids, *([since] if since else [])]
        count_expr = "count(DISTINCT ydid)" if "ydid" in columns else "count(*)"
        rows = conn.execute(
            f"""
        SELECT
            project_id,
            dispatch_train_code,
            COALESCE(ship_name, '') AS ship_name,
            COALESCE(destination_name, '') AS destination_name,
            date(min(ticketed_at)) AS ticket_date,
            substr(min(ticketed_at), 12, 8) AS ticket_time,
            min(ticketed_at) AS ticketed_at,
            {count_expr} AS car_count
        FROM {table}
        WHERE project_id IN ({ph})
          AND (loading_line IS NULL OR trim(loading_line) = '')
          AND COALESCE(ticketed_at, '') != ''
          {where_since}
        GROUP BY project_id, date(ticketed_at), dispatch_train_code, ship_name, destination_name
        ORDER BY ticketed_at, dispatch_train_code
        """,
            params,
        ).fetchall()
        groups.extend(
            MissingTrainGroup(
                source_table=table,
                project_id=r[0],
                dispatch_train_code=r[1] or "",
                ship_name=r[2] or "",
                destination_name=r[3] or "",
                ticket_date=r[4] or "",
                ticket_time=r[5] or "",
                ticketed_at=r[6] or "",
                car_count=int(r[7] or 0),
            )
            for r in rows
        )
    return sorted(groups, key=lambda group: (group.ticketed_at, group.source_table))


def _minutes_between(a: str, b: str) -> int:
    try:
        da = datetime.fromisoformat(a)
        db = datetime.fromisoformat(b)
    except Exception:
        return 999999
    return abs(int((da - db).total_seconds() // 60))


def _candidate_score(group: MissingTrainGroup, seg: ParsedSegment, received_datetime: str) -> int:
    score = 0
    if seg.project_id == group.project_id:
        score += 40
    if group.ship_name and seg.ship_name == group.ship_name:
        score += 40
    elif not group.ship_name and not seg.ship_name:
        score += 10
    elif not group.ship_name and seg.ship_name:
        score += 20
    if seg.car_count == group.car_count:
        score += 30
    if seg.destination_name and seg.destination_name == group.destination_name:
        score += 10
    score -= min(_minutes_between(group.ticketed_at, received_datetime), 24 * 60)
    return score


def _compatible_segment(group: MissingTrainGroup, seg: ParsedSegment) -> bool:
    if not seg.lane or seg.car_count != group.car_count:
        return False
    if seg.project_id and seg.project_id != group.project_id:
        return False
    if group.ship_name and seg.ship_name and seg.ship_name != group.ship_name:
        return False
    if group.ship_name and not seg.ship_name:
        return False
    return True


def _chat_records_root() -> Path:
    # Sibling repo layout: …/repos/sop-data-hub + …/repos/wx-ops-agent
    return Path(__file__).resolve().parents[4] / "wx-ops-agent" / "data" / "chat_records"


def _iter_chat_record_texts(
    group_name: str,
    *,
    start: str,
    end: str,
) -> list[tuple[int, str, str]]:
    """Load text messages from wx-ops-agent monthly jsonl ledgers.

    Returns list of (synthetic_id, received_datetime, text). Used when
    message_inbox misses historical lines still present in chat_records.
    """
    root = _chat_records_root() / group_name
    if not root.is_dir():
        return []
    out: list[tuple[int, str, str]] = []
    seq = 0
    for path in sorted(root.glob("202*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    import json as _json

                    row = _json.loads(line)
                except Exception:
                    continue
                if (row.get("msg-type") or row.get("msg_type") or "") != "text":
                    continue
                text = (row.get("msg-content") or row.get("msg_content") or "").strip()
                if not text:
                    continue
                t = row.get("time") or row.get("received_datetime") or ""
                if isinstance(t, (int, float)) or (isinstance(t, str) and t.isdigit()):
                    try:
                        t = datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
                t = str(t).replace("T", " ")[:19]
                if t < start[:19] or t > end[:19]:
                    continue
                seq += 1
                out.append((-seq, t, text))  # negative id = not inbox
        except OSError:
            continue
    return out


def find_candidates_for_group(
    conn: sqlite3.Connection,
    group: MissingTrainGroup,
    *,
    group_name: str,
    window_before_hours: int = 18,
    window_after_hours: int = 8,
) -> list[CandidateMatch]:
    try:
        ticket_dt = datetime.fromisoformat(group.ticketed_at)
    except ValueError:
        return []
    start = (ticket_dt - timedelta(hours=window_before_hours)).strftime("%Y-%m-%d %H:%M:%S")
    end = (ticket_dt + timedelta(hours=window_after_hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT id, received_datetime, text_content
        FROM message_inbox
        WHERE group_name = ?
          AND msg_type = 'text'
          AND received_datetime BETWEEN ? AND ?
        ORDER BY received_datetime
        """,
        (group_name, start, end),
    ).fetchall()

    # Supplement with chat_records ledger (covers texts never ingested to inbox).
    seen_texts = {(str(r[1] or "")[:19], (r[2] or "").strip()) for r in rows}
    for syn_id, received_datetime, text_content in _iter_chat_record_texts(
        group_name, start=start, end=end
    ):
        key = (received_datetime[:19], text_content.strip())
        if key in seen_texts:
            continue
        seen_texts.add(key)
        rows = list(rows)
        rows.append((syn_id, received_datetime, text_content))

    out: list[CandidateMatch] = []
    for inbox_id, received_datetime, text_content in rows:
        for seg in parse_workgroup_segments(text_content or ""):
            if not _compatible_segment(group, seg):
                continue
            out.append(
                CandidateMatch(
                    group=group,
                    inbox_id=int(inbox_id),
                    received_datetime=received_datetime or "",
                    lane=seg.lane,
                    ship_name=seg.ship_name,
                    car_count=seg.car_count,
                    text_preview=(text_content or "").strip().replace("\n", " ")[:120],
                    score=_candidate_score(group, seg, received_datetime or ""),
                )
            )
    out.sort(key=lambda item: item.score, reverse=True)
    return out


def decide_backfill(
    conn: sqlite3.Connection,
    *,
    project_ids: Iterable[str],
    group_name: str = "",
    since: str = "",
) -> list[BackfillDecision]:
    decisions: list[BackfillDecision] = []
    for group in load_missing_train_groups(conn, project_ids=project_ids, since=since):
        source_group = group_name or PROJECT_GROUPS.get(group.project_id, "铁晟业务工作群")
        candidates = find_candidates_for_group(conn, group, group_name=source_group)
        if not candidates:
            if group.project_id == "jiusan":
                decisions.append(
                    BackfillDecision(
                        group=group,
                        status="fallback",
                        lane=_JIUSAN_FALLBACK_LINES[group.source_table],
                        note="jiusan_user_confirmed_mode_default",
                    )
                )
                continue
            decisions.append(
                BackfillDecision(
                    group=group,
                    status="unmatched",
                    candidate_count=0,
                    note="no candidate text in time window",
                )
            )
            continue
        lanes = {c.lane for c in candidates}
        if len(lanes) != 1:
            decisions.append(
                BackfillDecision(
                    group=group,
                    status="ambiguous",
                    candidate_count=len(candidates),
                    note="multiple candidate lanes",
                )
            )
            continue
        top = candidates[0]
        decisions.append(
            BackfillDecision(
                group=group,
                status="matched",
                lane=top.lane,
                inbox_id=top.inbox_id,
                received_datetime=top.received_datetime,
                text_preview=top.text_preview,
                candidate_count=len(candidates),
            )
        )
    return decisions


def apply_backfill(
    conn: sqlite3.Connection,
    decisions: Iterable[BackfillDecision],
) -> int:
    applied = 0
    for decision in decisions:
        if decision.status not in {"matched", "fallback"} or not decision.lane:
            continue
        if decision.group.source_table not in _LEDGER_TABLES:
            continue
        where_train = "dispatch_train_code = ?" if decision.group.dispatch_train_code else "date(ticketed_at) = ?"
        train_value = decision.group.dispatch_train_code or decision.group.ticket_date
        ship_filter = " AND COALESCE(ship_name, '') = ?" if decision.group.ship_name else ""
        ship_params = [decision.group.ship_name] if decision.group.ship_name else []
        cur = conn.execute(
            f"""
            UPDATE {decision.group.source_table}
            SET loading_line = ?, updated_at = CURRENT_TIMESTAMP
            WHERE {where_train}
              AND project_id = ?
              {ship_filter}
              AND (loading_line IS NULL OR trim(loading_line) = '')
            """,
            (
                decision.lane,
                train_value,
                decision.group.project_id,
                *ship_params,
            ),
        )
        applied += cur.rowcount
    return applied


def sync_recent_loading_lines(
    db_path: str | Path | None = None,
    *,
    since: str = "",
    project_ids: Iterable[str] = tuple(PROJECT_GROUPS),
) -> dict[str, int]:
    """Apply the maintained text-to-line reconciliation for recent shipment rows.

    This is safe for the text-watch daemon: it only fills blank `loading_line`
    fields and never rewrites an existing business line.
    """
    if not since:
        from sop_hub.utils.time import now_iso_beijing

        current = datetime.fromisoformat(now_iso_beijing())
        since = (current - timedelta(days=14)).date().isoformat()
    conn = sqlite3.connect(str(_db_path(db_path)))
    try:
        decisions = decide_backfill(conn, project_ids=project_ids, since=since)
        applied = apply_backfill(conn, decisions)
        conn.commit()
    finally:
        conn.close()
    return {
        "groups": len(decisions),
        "matched": sum(d.status == "matched" for d in decisions),
        "fallback": sum(d.status == "fallback" for d in decisions),
        "unmatched": sum(d.status == "unmatched" for d in decisions),
        "ambiguous": sum(d.status == "ambiguous" for d in decisions),
        "applied_rows": applied,
    }


def apply_jiusan_historical_line_defaults(conn: sqlite3.Connection) -> dict[str, int]:
    """Apply the user-confirmed historical Jiusan line convention to blank rows.

    Bulk wagons are always line 7. A container ticket uses line 8 when there
    is any bulk-wagon handling record on the same ticket date; otherwise it
    uses line 7. Existing lines are immutable.
    """
    bulk = conn.execute(
        """
        UPDATE wagon_shipments
        SET loading_line='七道', updated_at=CURRENT_TIMESTAMP
        WHERE project_id='jiusan' AND trim(coalesce(loading_line,''))=''
        """
    ).rowcount
    container_eight = conn.execute(
        """
        UPDATE wagon_container_shipments AS wc
        SET loading_line='八道', updated_at=CURRENT_TIMESTAMP
        WHERE wc.project_id='jiusan'
          AND trim(coalesce(wc.loading_line,''))=''
          AND EXISTS (
              SELECT 1 FROM wagon_shipments AS ws
              WHERE ws.project_id='jiusan'
                AND date(ws.ticketed_at)=date(wc.ticketed_at)
          )
        """
    ).rowcount
    container_seven = conn.execute(
        """
        UPDATE wagon_container_shipments
        SET loading_line='七道', updated_at=CURRENT_TIMESTAMP
        WHERE project_id='jiusan' AND trim(coalesce(loading_line,''))=''
        """
    ).rowcount
    return {
        "bulk_seven": bulk,
        "container_eight": container_eight,
        "container_seven": container_seven,
    }
