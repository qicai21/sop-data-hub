#!/usr/bin/env python3
"""九三大豆 route A 集装箱费用生成。

按 release_batch(lot01) 生成：
  - fee_item_catalog 种子
  - fee_batch
  - fee_batch_member
  - fee_record

依赖：
  - sop-data-hub/data/sop_agent.db
  - rail95306-sync/runtime/95306_collection.sqlite3(只读)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sop_hub.fees.jiusan_container import (  # noqa: E402
    calc_route_a_fee_items,
    container_billing_weight,
    load_route_a_config,
    stable_hash,
)
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

DB = REPO / "data" / "sop_agent.db"
RAIL_DB = REPO.parents[0] / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
PROJECT = "jiusan"
LOT = "lot01"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_catalog(conn: sqlite3.Connection, config: dict, now: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for item in config.get("items") or []:
        fid = stable_hash(PROJECT, "fee_item", item["code"])
        ids[item["code"]] = fid
        conn.execute(
            """INSERT OR REPLACE INTO fee_item_catalog
               (id, project_id, fee_code, fee_name, charge_side, pricing_unit,
                default_rate, tax_rate, document_flow_type, contract_ref,
                contract_path, enabled, source_mode, source_ref, created_by,
                updated_by, created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fid,
                PROJECT,
                item["code"],
                item["name"],
                item["side"],
                "ton" if item.get("base") == "railway_billing_weight" else "box",
                item.get("rate"),
                item.get("tax"),
                "",
                "JGWL-JZTS-DD-202601",
                str(REPO / "config" / "project_sops" / "jiusan.yaml"),
                1,
                "system_generated",
                "jiusan.yaml:cost_structure.route_a_container",
                "codex",
                "codex",
                now,
                now,
                "",
            ),
        )
    return ids


def _release_batches(conn: sqlite3.Connection, ship: str) -> list[sqlite3.Row]:
    sql = """
        SELECT id, ship_name, batch_sequence, notice_date, destination_station
        FROM release_batches
        WHERE project=? AND batch_sequence=? AND transport_mode LIKE '%集装箱%'
    """
    params: list[str] = [PROJECT, LOT]
    if ship:
        sql += " AND ship_name=?"
        params.append(ship)
    sql += " ORDER BY notice_date, ship_name"
    return conn.execute(sql, tuple(params)).fetchall()


def _members(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, ydid, car_no, box_no, box_position, marked_weight, ticketed_at,
               departed_at, arrived_at, delivered_at, accepted_at, loaded_at,
               latest_stage_key, latest_stage_name, latest_event_time, ship_name
        FROM wagon_container_shipments
        WHERE batch_id=?
        ORDER BY ticketed_at, car_no, box_no, ydid
        """,
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _freight_summary(conn: sqlite3.Connection, batch_id: str) -> tuple[float, int]:
    rail = sqlite3.connect(str(RAIL_DB))
    rail.row_factory = sqlite3.Row
    try:
        rail.execute("ATTACH ? AS sop", (str(DB),))
        row = rail.execute(
            """
            SELECT round(sum(coalesce(s.freight_fee,0))/100.0, 2) AS freight_yuan,
                   count(distinct s.ydid) AS ydid_count
            FROM shipments s
            WHERE s.ydid IN (
              SELECT distinct ydid FROM sop.wagon_container_shipments WHERE batch_id=?
            )
            """,
            (batch_id,),
        ).fetchone()
        return float(row["freight_yuan"] or 0.0), int(row["ydid_count"] or 0)
    finally:
        rail.close()


def _upsert_batch(
    conn: sqlite3.Connection,
    *,
    batch: sqlite3.Row,
    cfg: dict,
    item_ids: dict[str, str],
    now: str,
) -> dict:
    members = _members(conn, batch["id"])
    if not members:
        return {}

    batch_id = stable_hash(PROJECT, "container_fee_batch", batch["ship_name"], LOT)
    source_ref = f"wagon_container_shipments:{batch['ship_name']}:{LOT}:{batch['id']}"
    box_count = len({m["box_no"] for m in members if m.get("box_no")})
    wagon_count = len({m["car_no"] for m in members if m.get("car_no")})
    total_weight = container_billing_weight(box_count, float(cfg.get("weight_per_box", 28.4)))
    freight_sum, ydid_count = _freight_summary(conn, batch["id"])
    event_date = min((str(m.get("ticketed_at") or "")[:10] for m in members if m.get("ticketed_at")), default=batch["notice_date"])

    conn.execute("DELETE FROM fee_batch_member WHERE fee_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM fee_record WHERE fee_batch_id=?", (batch_id,))

    conn.execute(
        """INSERT OR REPLACE INTO fee_batch
           (id, project_id, batch_type, mode, route_code, release_batch_id, ship_name,
            location_code, yard_line, event_date, period_start, period_end, wagon_count,
            container_count, weight_basis, total_weight, recognition_scope, recognition_key,
            status, source_mode, source_ref, created_by, updated_by, created_at, updated_at, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            batch_id,
            PROJECT,
            "container_dispatch",
            "container",
            cfg.get("route_code", "A"),
            batch["id"],
            batch["ship_name"],
            cfg.get("location_code", "三三〇处专用线"),
            cfg.get("location_code", "三三〇处专用线"),
            event_date,
            event_date,
            event_date,
            wagon_count,
            box_count,
            "billing_weight",
            total_weight,
            cfg.get("recognition_scope", "container_batch"),
            f"{batch['ship_name']}|{LOT}|route_a",
            "cost_generated",
            "system_generated",
            source_ref,
            "codex",
            "codex",
            now,
            now,
            f"ydid_count={ydid_count}",
        ),
    )

    for m in members:
        mid = stable_hash(batch_id, m["id"])
        conn.execute(
            """INSERT OR REPLACE INTO fee_batch_member
               (id, fee_batch_id, unit_type, wagon_shipment_id, wagon_container_shipment_id, ydid,
                car_no, box_no, marked_weight, settlement_weight, billing_weight, ticketed_at,
                departed_at, arrived_at, delivered_at, source_mode, source_ref, created_by,
                updated_by, created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                batch_id,
                "container",
                None,
                m["id"],
                m.get("ydid"),
                m.get("car_no"),
                m.get("box_no"),
                m.get("marked_weight"),
                None,
                float(cfg.get("weight_per_box", 28.4)),
                m.get("ticketed_at"),
                m.get("departed_at"),
                m.get("arrived_at"),
                m.get("delivered_at"),
                "system_generated",
                source_ref,
                "codex",
                "codex",
                now,
                now,
                f"box_position={m.get('box_position')}",
            ),
        )

    fee_items = calc_route_a_fee_items(
        config=cfg,
        box_count=box_count,
        total_weight=total_weight,
        freight_sum_yuan=freight_sum,
        source_ref=source_ref,
    )
    for item in fee_items:
        rid = stable_hash(batch_id, "fee_record", item.code)
        conn.execute(
            """INSERT OR REPLACE INTO fee_record
               (id, fee_batch_id, fee_item_id, fee_code, fee_name_snapshot, charge_side, counterparty,
                settle_party, price, qty, qty_unit, amount, pricing_basis, pricing_basis_value,
                recognition_scope, recognition_key, document_flow_type, status, reconciliation_id,
                source_mode, source_ref, version_no, created_by, updated_by, created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                batch_id,
                item_ids[item.code],
                item.code,
                item.name,
                item.side,
                item.counterparty,
                item.settle_party,
                item.price,
                item.qty,
                item.qty_unit,
                item.amount,
                item.pricing_basis,
                item.pricing_basis_value,
                cfg.get("recognition_scope", "container_batch"),
                f"{batch['ship_name']}|{LOT}|route_a",
                "",
                "generated",
                None,
                "system_generated",
                source_ref,
                1,
                "codex",
                "codex",
                now,
                now,
                "",
            ),
        )
    return {
        "batch_id": batch_id,
        "ship_name": batch["ship_name"],
        "wagon_count": wagon_count,
        "box_count": box_count,
        "total_weight": total_weight,
        "freight_sum": freight_sum,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    now = now_iso_beijing()
    cfg = load_route_a_config()
    if not cfg:
        raise SystemExit("jiusan.yaml 缺 cost_structure.route_a_container")
    if not RAIL_DB.exists():
        raise SystemExit(f"缺少 95306 只读库: {RAIL_DB}")

    conn = _open()
    try:
        item_ids = _ensure_catalog(conn, cfg, now)
        batches = _release_batches(conn, args.ship)
        outputs = []
        for batch in batches:
            out = _upsert_batch(conn, batch=batch, cfg=cfg, item_ids=item_ids, now=now)
            if out:
                outputs.append(out)
        if args.apply:
            conn.commit()
            print(f"COMMIT ✓ 生成 {len(outputs)} 个九三 route A 集装箱费用批次")
        else:
            conn.rollback()
            print(f"DRY-RUN: 将生成 {len(outputs)} 个九三 route A 集装箱费用批次")
        for o in outputs:
            print(
                f"  ship={o['ship_name']} batch={o['batch_id']} wagons={o['wagon_count']} "
                f"boxes={o['box_count']} weight={o['total_weight']} freight={o['freight_sum']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
