"""Join a text trigger to one compatible inspection-image candidate."""
from __future__ import annotations

import sqlite3


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
