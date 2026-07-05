"""Stable same-train marker for wagon shipment rows.

`trip_seq` is scoped to a release batch, so it cannot represent a physical
train that crosses multiple lots or ships. `dispatch_train_code` is the shared
business key used by fee review, settlement, onsite receipts, and dashboard
queries.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


PROJECT_PREFIXES = {
    "zhongtang_special_steel": "gqz",
    "chaoyang_steel": "cg",
    "jilin_jingang_jinzhou": "jg",
    "jiusan": "js",
}

TABLES = ("wagon_shipments", "wagon_container_shipments")


@dataclass
class TrainRow:
    table: str
    row_id: str
    project_id: str
    batch_id: str
    source_message_id: str
    source_group_id: str
    loading_line: str
    destination_name: str
    event_date: str
    event_time: str
    event_dt: datetime | None
    existing_code: str


@dataclass
class TrainGroup:
    project_id: str
    event_date: str
    group_key: str
    min_time: str
    rows: list[TrainRow] = field(default_factory=list)
    existing_codes: list[str] = field(default_factory=list)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def ensure_dispatch_train_schema(conn: sqlite3.Connection) -> None:
    """Ensure both wagon tables can store a stable same-train marker."""
    for table in TABLES:
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        if "dispatch_train_code" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dispatch_train_code TEXT")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_dispatch_train_code "
            f"ON {table}(dispatch_train_code)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_project_train_date "
            f"ON {table}(project_id, dispatch_train_code)"
        )


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    m = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?",
        value,
    )
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        return datetime(
            int(y),
            int(mo),
            int(d),
            int(hh or 0),
            int(mm or 0),
            int(ss or 0),
        )
    m = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", value)
    if m:
        y, mo, d = m.groups()
        return datetime(int(y), int(mo), int(d))
    return None


def _first_date(*values: str) -> tuple[str, str, datetime | None]:
    for value in values:
        value = (value or "").strip()
        if not value:
            continue
        dt = _parse_dt(value)
        if dt:
            return dt.date().isoformat(), value, dt
    return "1970-01-01", "", None


def _code_date(date: str) -> str:
    if len(date) >= 10 and date[:4].isdigit():
        return f"{date[2:4]}{date[5:7]}{date[8:10]}"
    return "000000"


def _seq_token(n: int) -> str:
    alphabet = "123456789abcdefghijklmnopqrstuvwxyz"
    if n <= len(alphabet):
        return alphabet[n - 1]
    # Extremely rare, but keep deterministic instead of failing.
    base = "0123456789abcdefghijklmnopqrstuvwxyz"
    x = n
    out = ""
    while x:
        x, r = divmod(x, 36)
        out = base[r] + out
    return out


def _project_prefix(project_id: str) -> str:
    if project_id in PROJECT_PREFIXES:
        return PROJECT_PREFIXES[project_id]
    letters = "".join(ch for ch in (project_id or "") if ch.isalnum()).lower()
    return (letters[:3] or "prj")


def _is_reliable_train_source(source_message_id: str) -> bool:
    source = (source_message_id or "").strip()
    if not source:
        return False
    # Legacy/manual backfills often stamped one source per waybill, e.g.
    # `backfill_remaining|非凡|GZDZW0438945`; those are not train-level sources.
    if "backfill" in source.lower():
        return False
    return True


def _static_group_key(row: TrainRow) -> str:
    return ":".join([
        "time",
        row.project_id,
        row.event_date,
        row.batch_id,
        row.loading_line,
        row.destination_name,
    ])


def _group_key(row: TrainRow) -> str | None:
    if row.source_message_id and not _is_reliable_train_source(row.source_message_id):
        return None
    if row.source_message_id:
        return f"src:{row.project_id}:{row.source_message_id}"
    if row.source_group_id:
        return f"group:{row.project_id}:{row.source_group_id}:{row.event_date}"
    return None


def _iter_rows(conn: sqlite3.Connection, tables: Iterable[str]) -> list[TrainRow]:
    rows: list[TrainRow] = []
    for table in tables:
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        required = {"id", "project_id", "batch_id"}
        if not required <= cols:
            continue
        select_cols = [
            "id",
            "project_id",
            "batch_id",
            "source_message_id" if "source_message_id" in cols else "'' AS source_message_id",
            "source_group_id" if "source_group_id" in cols else "'' AS source_group_id",
            "loading_line" if "loading_line" in cols else "'' AS loading_line",
            "destination_name" if "destination_name" in cols else "'' AS destination_name",
            "departed_at" if "departed_at" in cols else "'' AS departed_at",
            "loaded_at" if "loaded_at" in cols else "'' AS loaded_at",
            "ticketed_at" if "ticketed_at" in cols else "'' AS ticketed_at",
            "accepted_at" if "accepted_at" in cols else "'' AS accepted_at",
            "created_at" if "created_at" in cols else "'' AS created_at",
            "dispatch_train_code" if "dispatch_train_code" in cols else "'' AS dispatch_train_code",
        ]
        query = f"SELECT {', '.join(select_cols)} FROM {table}"
        for r in conn.execute(query):
            date, event_time, event_dt = _first_date(
                # Same-train boundaries are defined by nearby ticketing time;
                # date-only loaded/departed fields must not override it.
                r["ticketed_at"],
                r["departed_at"],
                r["loaded_at"],
                r["accepted_at"],
                r["created_at"],
            )
            project_id = (r["project_id"] or "").strip()
            if not project_id:
                continue
            rows.append(
                TrainRow(
                    table=table,
                    row_id=r["id"],
                    project_id=project_id,
                    batch_id=(r["batch_id"] or "").strip(),
                    source_message_id=(r["source_message_id"] or "").strip(),
                    source_group_id=(r["source_group_id"] or "").strip(),
                    loading_line=(r["loading_line"] or "").strip(),
                    destination_name=(r["destination_name"] or "").strip(),
                    event_date=date,
                    event_time=event_time,
                    event_dt=event_dt,
                    existing_code=(r["dispatch_train_code"] or "").strip(),
                )
            )
    return rows


def _build_groups(
    rows: list[TrainRow],
    *,
    max_gap_minutes: int,
    max_span_minutes: int,
) -> dict[str, TrainGroup]:
    groups: dict[str, TrainGroup] = {}
    temporal_rows: list[TrainRow] = []
    for row in rows:
        key = _group_key(row)
        if key is None:
            temporal_rows.append(row)
            continue
        _add_to_group(groups, key, row)

    buckets: dict[str, list[TrainRow]] = {}
    for row in temporal_rows:
        buckets.setdefault(_static_group_key(row), []).append(row)

    for bucket_key, bucket_rows in buckets.items():
        bucket_rows.sort(key=lambda r: (r.event_dt or datetime.min, r.event_time, r.row_id))
        cluster_no = 0
        last_dt: datetime | None = None
        cluster_start_dt: datetime | None = None
        for row in bucket_rows:
            should_start = (
                last_dt is None
                or row.event_dt is None
                or (row.event_dt - last_dt).total_seconds() > max_gap_minutes * 60
                or (
                    cluster_start_dt is not None
                    and (row.event_dt - cluster_start_dt).total_seconds() > max_span_minutes * 60
                )
            )
            if should_start:
                cluster_no += 1
                cluster_start_dt = row.event_dt
            key = f"{bucket_key}:cluster:{cluster_no}"
            _add_to_group(groups, key, row)
            if row.event_dt is not None:
                last_dt = row.event_dt
    return groups


def _add_to_group(groups: dict[str, TrainGroup], key: str, row: TrainRow) -> None:
    g = groups.get(key)
    if g is None:
        g = TrainGroup(
            project_id=row.project_id,
            event_date=row.event_date,
            group_key=key,
            min_time=row.event_time,
        )
        groups[key] = g
    if row.event_time and (not g.min_time or row.event_time < g.min_time):
        g.min_time = row.event_time
    if row.event_date < g.event_date:
        g.event_date = row.event_date
    if row.existing_code:
        g.existing_codes.append(row.existing_code)
    g.rows.append(row)


def assign_dispatch_train_codes(
    conn: sqlite3.Connection,
    *,
    tables: Iterable[str] = TABLES,
    overwrite: bool = False,
    max_gap_minutes: int = 30,
    max_span_minutes: int = 60,
) -> dict[str, int]:
    """Fill `dispatch_train_code` for existing wagon rows.

    Priority:
    1. Reliable same `source_message_id` = same physical departure train, even
       across release batches or ship names.
    2. Existing code on any row in that group is preserved and propagated.
    3. Missing/unreliable-source legacy rows fall back to nearby ticket time
       clustering within a project/date/destination/loading-line bucket.
    """
    conn.row_factory = sqlite3.Row
    ensure_dispatch_train_schema(conn)
    rows = _iter_rows(conn, tables)

    groups = _build_groups(
        rows,
        max_gap_minutes=max_gap_minutes,
        max_span_minutes=max_span_minutes,
    )

    used_by_day: dict[tuple[str, str], set[str]] = {}
    if not overwrite:
        for g in groups.values():
            for code in g.existing_codes:
                used_by_day.setdefault((g.project_id, g.event_date), set()).add(code)

    updates = 0
    by_day: dict[tuple[str, str], list[TrainGroup]] = {}
    for g in groups.values():
        by_day.setdefault((g.project_id, g.event_date), []).append(g)

    for (project_id, event_date), day_groups in by_day.items():
        day_groups.sort(key=lambda g: (g.min_time or "", g.group_key))
        prefix = _project_prefix(project_id)
        used = used_by_day.setdefault((project_id, event_date), set())
        next_seq = 1
        for g in day_groups:
            if g.existing_codes and not overwrite:
                code = sorted(g.existing_codes)[0]
            else:
                while True:
                    code = f"{prefix}{_code_date(event_date)}{_seq_token(next_seq)}"
                    next_seq += 1
                    if code not in used:
                        break
            used.add(code)
            for row in g.rows:
                if not overwrite and row.existing_code == code:
                    continue
                if not overwrite and row.existing_code and row.existing_code != code:
                    continue
                conn.execute(
                    f"UPDATE {row.table} SET dispatch_train_code=? WHERE id=?",
                    (code, row.row_id),
                )
                updates += 1

    return {"groups": len(groups), "rows": len(rows), "updated": updates}
