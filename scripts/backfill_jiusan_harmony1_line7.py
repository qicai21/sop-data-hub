#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"

PROJECT = "jiusan"
SHIP_NAME = "和谐1"
ROUTE_A_BATCH_ID = "e96f4b3b83c74b4891c6b0957f6989bb827de45b"
LOT = "lot02"
LINE_NAME = "七道"
OLD_PLACEHOLDER_TRACK = "和谐1"


def main() -> None:
    conn = sqlite3.connect(str(DB))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE bulk_loading_notice_wagon
            SET track=?
            WHERE project=? AND ship_name=? AND lot=?
            """,
            (LINE_NAME, PROJECT, SHIP_NAME, LOT),
        )
        bulk_rows = cur.rowcount

        cur.execute(
            """
            UPDATE wagon_container_shipments
            SET loading_line=?
            WHERE project_id=? AND ship_name=? AND batch_id=?
            """,
            (LINE_NAME, PROJECT, SHIP_NAME, ROUTE_A_BATCH_ID),
        )
        container_rows = cur.rowcount

        old_batch_ids = [
            row[0]
            for row in cur.execute(
                """
                SELECT id
                FROM fee_batch
                WHERE project_id=? AND ship_name=? AND route_code='C' AND yard_line=?
                """,
                (PROJECT, SHIP_NAME, OLD_PLACEHOLDER_TRACK),
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
            f"APPLIED: bulk_loading_notice_wagon={bulk_rows} rows, "
            f"wagon_container_shipments={container_rows} rows, "
            f"deleted_old_route_c_batches={len(old_batch_ids)}, line={LINE_NAME}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
