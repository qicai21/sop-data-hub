#!/usr/bin/env python3
"""按船汇总九三费用并回写 billing_reconciliation。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sop_hub.fees.jiusan_bulk import stable_hash  # noqa: E402
from sop_hub.fees.reconciliation import group_ship_fee_rows  # noqa: E402
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

DB = REPO / "data" / "sop_agent.db"
PROJECT = "jiusan"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, ship: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT fr.id AS fee_record_id, fr.fee_batch_id, fr.fee_code, fr.charge_side, fr.counterparty,
               fr.settle_party, fr.amount, fb.wagon_count, fb.container_count, fb.total_weight
        FROM fee_record fr
        JOIN fee_batch fb ON fb.id = fr.fee_batch_id
        WHERE fb.project_id=? AND fb.ship_name=? AND fr.status<>'void'
        ORDER BY fr.fee_batch_id, fr.fee_code
        """,
        (PROJECT, ship),
    ).fetchall()
    return [dict(r) for r in rows]


def _upsert(conn: sqlite3.Connection, *, ship: str, now: str) -> list[dict]:
    rows = _rows(conn, ship)
    groups = group_ship_fee_rows(rows)
    out: list[dict] = []
    for g in groups:
        rid = stable_hash("billing_reconciliation", PROJECT, ship, g.charge_side, g.party_name)
        conn.execute("DELETE FROM billing_reconciliation WHERE id=?", (rid,))
        conn.execute(
            """INSERT INTO billing_reconciliation
               (id, project, counterparty, period, total_amount, total_cars, total_qty, status,
                confirmed_at, note, created_at, updated_at, recognition_scope, recognition_key,
                source_mode, source_ref, created_by, updated_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                PROJECT,
                g.party_name,
                f"ship:{ship}",
                g.total_amount,
                g.wagon_count,
                g.total_weight,
                "已发对账",
                None,
                (
                    f"charge_side={g.charge_side}; fee_codes={','.join(g.fee_codes)}; "
                    f"batch_count={len(g.batch_ids)}; container_count={g.container_count}"
                ),
                now,
                now,
                "ship",
                ship,
                "system_generated",
                f"fee_ship_rollup:{ship}",
                "codex",
                "codex",
            ),
        )
        conn.execute(
            """
            UPDATE fee_record
            SET reconciliation_id=?, updated_at=?, updated_by='codex'
            WHERE id IN (
              SELECT fr.id
              FROM fee_record fr
              JOIN fee_batch fb ON fb.id=fr.fee_batch_id
              WHERE fb.project_id=? AND fb.ship_name=?
                AND fr.status<>'void'
                AND fr.charge_side=?
                AND coalesce(CASE WHEN fr.charge_side='income' THEN fr.counterparty ELSE fr.settle_party END,'')=?
            )
            """,
            (rid, now, PROJECT, ship, g.charge_side, g.party_name),
        )
        out.append(
            {
                "id": rid,
                "charge_side": g.charge_side,
                "party_name": g.party_name,
                "total_amount": g.total_amount,
                "wagon_count": g.wagon_count,
                "container_count": g.container_count,
                "total_weight": g.total_weight,
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = _open()
    now = now_iso_beijing()
    try:
        out = _upsert(conn, ship=args.ship, now=now)
        if args.apply:
            conn.commit()
            print(f"COMMIT ✓ 生成 {len(out)} 条 ship 对账记录")
        else:
            conn.rollback()
            print(f"DRY-RUN: 将生成 {len(out)} 条 ship 对账记录")
        for row in out:
            print(
                f"  {row['charge_side']} | {row['party_name']} | amount={row['total_amount']} "
                f"| wagons={row['wagon_count']} | boxes={row['container_count']} | weight={row['total_weight']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
