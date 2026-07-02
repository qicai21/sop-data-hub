#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"

PROJECT = "jiusan"
LOT = "lot02"


def _route_a_batch_id(conn: sqlite3.Connection, ship_name: str) -> str:
    row = conn.execute(
        """
        SELECT id
        FROM release_batches
        WHERE project=? AND ship_name=? AND batch_sequence='lot01'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (PROJECT, ship_name),
    ).fetchone()
    return str(row[0]) if row else ""


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", default="和谐1")
    ap.add_argument("--line", default="七道")
    ap.add_argument("--placeholder-track", default="")
    ap.add_argument("--route-a-batch-id", default="")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    ship_name = str(args.ship or "").strip()
    line_name = str(args.line or "").strip()
    placeholder_track = str(args.placeholder_track or "").strip() or ship_name
    conn = sqlite3.connect(str(DB))
    try:
        cur = conn.cursor()
        route_a_batch_id = str(args.route_a_batch_id or "").strip() or _route_a_batch_id(conn, ship_name)
        cur.execute(
            """
            UPDATE bulk_loading_notice_wagon
            SET track=?
            WHERE project=? AND ship_name=? AND lot=?
            """,
            (line_name, PROJECT, ship_name, LOT),
        )
        bulk_rows = cur.rowcount

        if route_a_batch_id:
            cur.execute(
                """
                UPDATE wagon_container_shipments
                SET loading_line=?
                WHERE project_id=? AND ship_name=? AND batch_id=?
                """,
                (line_name, PROJECT, ship_name, route_a_batch_id),
            )
            container_rows = cur.rowcount
        else:
            container_rows = 0

        old_batch_ids = [
            row[0]
            for row in cur.execute(
                """
                SELECT id
                FROM fee_batch
                WHERE project_id=? AND ship_name=? AND route_code='C' AND yard_line=?
                """,
                (PROJECT, ship_name, placeholder_track),
            ).fetchall()
        ]
        for batch_id in old_batch_ids:
            doc_ids = [
                row[0]
                for row in cur.execute(
                    "SELECT id FROM doc_instance WHERE fee_batch_id=?",
                    (batch_id,),
                ).fetchall()
            ]
            for doc_id in doc_ids:
                cur.execute(
                    "DELETE FROM doc_instance_signoff WHERE doc_instance_id=?",
                    (doc_id,),
                )
            cur.execute("DELETE FROM doc_instance WHERE fee_batch_id=?", (batch_id,))
            cur.execute("DELETE FROM fee_batch_member WHERE fee_batch_id=?", (batch_id,))
            cur.execute("DELETE FROM fee_record WHERE fee_batch_id=?", (batch_id,))
            cur.execute("DELETE FROM fee_batch WHERE id=?", (batch_id,))

        conn.commit()
        print(
            f"ship={ship_name} route_a_batch_id={route_a_batch_id or '<none>'} "
            f"APPLIED: bulk_loading_notice_wagon={bulk_rows} rows, "
            f"wagon_container_shipments={container_rows} rows, "
            f"deleted_old_route_c_batches={len(old_batch_ids)}, line={line_name}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
