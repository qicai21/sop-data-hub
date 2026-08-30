"""Join a text trigger to one compatible inspection-image candidate."""
from __future__ import annotations

import sqlite3


def find_adjacent_text_expected_count(
    conn: sqlite3.Connection,
    *,
    inspection_inbox_id: int,
    project_id: str,
    ship_name: str,
    destination: str,
    max_distance_minutes: int = 10,
) -> dict[str, object] | None:
    """Return one trusted, adjacent inspection-text count for an image candidate.

    The text is only authoritative when it is in the same group and close to the
    image timestamp.  A unique match is deliberately required: old text messages
    and a second ship segment must never expand a Zhongtang same-window subset.
    """
    if not (inspection_inbox_id and project_id and ship_name and destination):
        return None
    try:
        image = conn.execute(
            "SELECT group_name, received_datetime FROM message_inbox WHERE id=?",
            (inspection_inbox_id,),
        ).fetchone()
        if not image or not image[0] or not image[1]:
            return None
        rows = conn.execute(
            "SELECT id, message_id, text_content, received_datetime FROM message_inbox "
            "WHERE id != ? AND group_name=? AND COALESCE(text_content, '') != '' "
            "AND datetime(substr(replace(received_datetime, 'T', ' '), 1, 19)) "
            "BETWEEN datetime(substr(replace(?, 'T', ' '), 1, 19), ?) "
            "AND datetime(substr(replace(?, 'T', ' '), 1, 19), ?) "
            "ORDER BY id",
            (
                inspection_inbox_id, image[0], image[1],
                f"-{max_distance_minutes} minutes", image[1],
                f"+{max_distance_minutes} minutes",
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    from sop_hub.sop.text_router import extract_inspection_text_triggers

    matches: list[dict[str, object]] = []
    for row in rows:
        for trigger in extract_inspection_text_triggers(str(row[2] or "")):
            if (
                trigger["project_id"] == project_id
                and trigger["ship"] == ship_name
                and trigger["destination"] == destination
                and int(trigger["expected_count"] or 0) > 0
            ):
                matches.append({
                    "expected_count": int(trigger["expected_count"]),
                    "message_inbox_id": int(row[0]),
                    "message_id": str(row[1] or ""),
                    "received_datetime": str(row[3] or ""),
                })
    if len(matches) != 1:
        return None
    return matches[0]


def enrich_unique_bare_candidate(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    ship_name: str,
    destination: str,
    expected_count: int,
    group_name: str,
    event_bounds: tuple[str, str] | None,
) -> sqlite3.Row | None:
    """Fill missing context only when one trusted image candidate matches exactly."""
    if not (
        project_id
        and ship_name
        and destination
        and expected_count > 0
        and group_name
        and event_bounds
    ):
        return None

    lo, hi = event_bounds
    try:
        rows = conn.execute(
            """
            SELECT c.*
            FROM inspection_ingestion_candidates c
            WHERE (COALESCE(c.ship_name, '') = '' OR c.ship_name = ?)
              AND (COALESCE(c.project_id, '') = '' OR c.project_id = ?)
              AND (COALESCE(c.destination, '') = '' OR c.destination = ?)
              AND c.candidate_status IN ('candidate', 'pending_match', 'pending_review')
              AND COALESCE(c.wagon_count, 0) = ?
              AND EXISTS (
                  SELECT 1
                  FROM message_inbox m
                  WHERE (m.inspection_candidate_id = c.id OR m.message_id = c.message_id)
                    AND m.group_name = ?
                    AND (
                        m.classification_label = '检装车通知单'
                        OR m.document_type = '检装车通知单'
                    )
                    AND datetime(substr(replace(m.received_datetime, 'T', ' '), 1, 19))
                        BETWEEN datetime(?) AND datetime(?)
              )
            ORDER BY datetime(substr(replace(c.created_at, 'T', ' '), 1, 19)) DESC
            """,
            (
                ship_name,
                project_id,
                destination,
                expected_count,
                group_name,
                lo,
                hi,
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        # Minimal legacy/test schemas may not expose the provenance columns.
        return None
    if len(rows) != 1:
        return None

    candidate_id = str(rows[0]["id"])
    conn.execute(
        """
        UPDATE inspection_ingestion_candidates
        SET project_id=?, ship_name=?, destination=?,
            candidate_status='candidate',
            reason='text_rendezvous_context_enriched',
            updated_at=datetime('now')
        WHERE id=?
        """,
        (project_id, ship_name, destination, candidate_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM inspection_ingestion_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
