#!/usr/bin/env python3
"""九三大豆 route A 集装箱费用生成。

按 release_batch(lot01) 生成：
  - fee_item_catalog 种子
  - fee_batch
  - fee_batch_member
  - fee_record

依赖：
  - sop-data-hub/data/sop_agent.db
  - rail95306-sync/runtime/95306_collection.sqlite3(仅历史补录 freight_fee/detail_json 用)
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
    load_contract_terms,
    load_line_rates,
    load_weight_confirmation,
    resolve_line_rate,
    stable_hash,
)
from sop_hub.fees.jiusan_container_types import (  # noqa: E402
    OPEN_TOP,
    TOP_OPEN,
    UNKNOWN_TYPE,
    classify_container_type,
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


ROUTE = "A"
REQUIRED_CODES = [
    "route_a_income",
    "route_a_nrf_cost",
    "route_a_metro_fee",
    "route_a_wagon_occupancy",
    "route_a_transfer_fee",
    "route_a_tarpaulin",
    "route_a_item9",
    "route_a_item10",
    "route_a_item11",
    "route_a_item13",
    "route_a_item18",
]


def _item_ids(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        f"""
        SELECT fee_code, id
        FROM fee_item_catalog
        WHERE project_id=? AND fee_code IN ({",".join("?" for _ in REQUIRED_CODES)})
        """,
        (PROJECT, *REQUIRED_CODES),
    ).fetchall()
    out = {str(r["fee_code"]): str(r["id"]) for r in rows}
    missing = [code for code in REQUIRED_CODES if code not in out]
    if missing:
        raise SystemExit(f"缺少 fee_item_catalog 费目: {missing}")
    return out


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
        SELECT id, ydid, car_no, box_no, box_position, marked_weight, freight_fee, loading_line, detail_json, ticketed_at,
               departed_at, arrived_at, delivered_at, accepted_at, loaded_at,
               latest_stage_key, latest_stage_name, latest_event_time, ship_name
        FROM wagon_container_shipments
        WHERE batch_id=?
        ORDER BY ticketed_at, car_no, box_no, ydid
        """,
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _backfill_local_ticket_fields(conn: sqlite3.Connection, batch_id: str) -> int:
    rows = conn.execute(
        """
        SELECT DISTINCT ydid
        FROM wagon_container_shipments
        WHERE batch_id=? AND (freight_fee IS NULL OR freight_fee=0 OR detail_json IS NULL OR detail_json='')
        """,
        (batch_id,),
    ).fetchall()
    ydids = [str(r["ydid"]) for r in rows if r["ydid"]]
    if not ydids or not RAIL_DB.exists():
        return 0
    ph = ",".join("?" for _ in ydids)
    rail = sqlite3.connect(str(RAIL_DB))
    rail.row_factory = sqlite3.Row
    updated = 0
    try:
        for r in rail.execute(
            f"""
            SELECT ydid, freight_fee, detail_json
            FROM shipments
            WHERE ydid IN ({ph})
            """,
            ydids,
        ).fetchall():
            cur = conn.execute(
                """
                UPDATE wagon_container_shipments
                SET freight_fee=COALESCE(NULLIF(freight_fee,0), ?),
                    detail_json=CASE WHEN detail_json IS NULL OR detail_json='' OR detail_json='{}' THEN ? ELSE detail_json END,
                    updated_at=?
                WHERE ydid=?
                """,
                (float(r["freight_fee"] or 0.0), str(r["detail_json"] or "{}"), now_iso_beijing(), str(r["ydid"])),
            )
            updated += cur.rowcount
    finally:
        rail.close()
    return updated


def _local_container_railway_allocations(conn: sqlite3.Connection, batch_id: str) -> tuple[float, float, int]:
    rows = conn.execute(
        """
        SELECT ydid,
               COUNT(*) AS batch_box_rows,
               MAX(COALESCE(marked_weight, 0)) AS marked_weight,
               MAX(COALESCE(freight_fee, 0)) AS freight_fee
        FROM wagon_container_shipments
        WHERE batch_id=?
        GROUP BY ydid
        """,
        (batch_id,),
    ).fetchall()
    if not rows:
        return 0.0, 0.0, 0

    ydids = [str(r["ydid"]) for r in rows if r["ydid"]]
    if not ydids:
        return 0.0, 0.0, 0
    placeholders = ",".join("?" for _ in ydids)

    total_counts = {
        str(r["ydid"]): int(r["total_box_rows"] or 0)
        for r in conn.execute(
            f"""
            SELECT ydid, COUNT(*) AS total_box_rows
            FROM wagon_container_shipments
            WHERE ydid IN ({placeholders})
            GROUP BY ydid
            """,
            ydids,
        ).fetchall()
    }
    freight_map = {
        str(r["ydid"]): float(r["freight_fee"] or 0.0) / 100.0
        for r in rows
    }

    allocated_weight = 0.0
    allocated_freight = 0.0
    for row in rows:
        ydid = str(row["ydid"])
        batch_box_rows = int(row["batch_box_rows"] or 0)
        total_box_rows = max(total_counts.get(ydid, 0), batch_box_rows, 1)
        share = float(batch_box_rows) / float(total_box_rows)
        allocated_weight += float(row["marked_weight"] or 0.0) * share
        allocated_freight += freight_map.get(ydid, 0.0) * share
    return round(allocated_weight, 2), round(allocated_freight, 2), len(rows)


def _line_rate_amount(
    conn: sqlite3.Connection,
    members: list[dict],
    batch_id: str,
    rate_map: dict[str, float],
    default_rate: float | None,
) -> tuple[float, list[str]]:
    batch_counts = {
        str(r["ydid"]): int(r["batch_box_rows"] or 0)
        for r in conn.execute(
            """
            SELECT ydid, COUNT(*) AS batch_box_rows
            FROM wagon_container_shipments
            WHERE batch_id=?
            GROUP BY ydid
            """,
            (batch_id,),
        ).fetchall()
    }
    total_counts = {
        str(r["ydid"]): int(r["total_box_rows"] or 0)
        for r in conn.execute(
            """
            SELECT ydid, COUNT(*) AS total_box_rows
            FROM wagon_container_shipments
            WHERE ydid IN (
              SELECT DISTINCT ydid FROM wagon_container_shipments WHERE batch_id=?
            )
            GROUP BY ydid
            """,
            (batch_id,),
        ).fetchall()
    }
    ydid_rows: dict[str, dict] = {}
    for m in members:
        ydid = str(m.get("ydid") or "")
        if ydid and ydid not in ydid_rows:
            ydid_rows[ydid] = m

    total = 0.0
    missing: list[str] = []
    seen_missing: set[str] = set()
    for ydid, m in ydid_rows.items():
        line_rate, line_name = resolve_line_rate(m.get("loading_line"), rate_map, default_rate)
        if line_rate is None:
            key = line_name or "<empty>"
            if key not in seen_missing:
                missing.append(key)
                seen_missing.add(key)
            continue
        share = float(batch_counts.get(ydid, 0)) / float(max(total_counts.get(ydid, 0), batch_counts.get(ydid, 0), 1))
        total += float(m.get("marked_weight") or 0.0) * share * float(line_rate)
    return round(total, 2), missing


def _container_type_counts(members: list[dict]) -> tuple[int, int]:
    counts = {OPEN_TOP: 0, TOP_OPEN: 0, UNKNOWN_TYPE: 0}
    unknown_boxes: list[str] = []
    for member in members:
        box_no = str(member.get("box_no") or "").strip()
        container_type = classify_container_type(box_no)
        counts[container_type] += 1
        if container_type == UNKNOWN_TYPE:
            unknown_boxes.append(box_no or "<empty>")
    if unknown_boxes:
        sample = ",".join(unknown_boxes[:10])
        raise SystemExit(f"存在无法识别箱型的箱号，不能计算第13项: count={len(unknown_boxes)} sample={sample}")
    return counts[OPEN_TOP], counts[TOP_OPEN]


def _upsert_batch(
    conn: sqlite3.Connection,
    *,
    batch: sqlite3.Row,
    item_ids: dict[str, str],
    now: str,
) -> dict:
    _backfill_local_ticket_fields(conn, batch["id"])
    members = _members(conn, batch["id"])
    if not members:
        return {}
    terms = load_contract_terms(conn, PROJECT, ROUTE)
    line_rates = load_line_rates(conn, PROJECT, ROUTE, "route_a_metro_fee")
    confirmed_weight = load_weight_confirmation(conn, batch["id"], ROUTE) or 0.0

    batch_id = stable_hash(PROJECT, "container_fee_batch", batch["ship_name"], LOT)
    source_ref = f"wagon_container_shipments:{batch['ship_name']}:{LOT}:{batch['id']}"
    box_count = len(members)
    open_top_count, top_open_count = _container_type_counts(members)
    wagon_count = len({m["car_no"] for m in members if m.get("car_no")})
    railway_weight, freight_sum, ydid_count = _local_container_railway_allocations(conn, batch["id"])
    metro_default = float((terms.get("route_a_metro_fee") or {}).get("default_rate") or 3.75)
    metro_amount, missing_lines = _line_rate_amount(conn, members, batch["id"], line_rates, metro_default)
    wagon_occupancy_rate = float((terms.get("route_a_wagon_occupancy") or {}).get("default_rate") or 0.64)
    wagon_occupancy_amount = round(railway_weight * wagon_occupancy_rate, 2)
    transfer_rate = float((terms.get("route_a_transfer_fee") or {}).get("default_rate") or 0.0)
    transfer_amount = round(box_count * transfer_rate, 2)
    tarpaulin_rate = float((terms.get("route_a_tarpaulin") or {}).get("default_rate") or 0.0)
    tarpaulin_amount = round(box_count * tarpaulin_rate * 0.7, 2)
    item9_rate = float((terms.get("route_a_item9") or {}).get("default_rate") or 0.0)
    item10_rate = float((terms.get("route_a_item10") or {}).get("default_rate") or 0.0)
    item11_rate = float((terms.get("route_a_item11") or {}).get("default_rate") or 0.0)
    item13_rate = float((terms.get("route_a_item13") or {}).get("default_rate") or 0.0)
    item18_rate = float((terms.get("route_a_item18") or {}).get("default_rate") or 0.0)
    item9_amount = round(box_count * item9_rate, 2)
    item10_amount = round(box_count * item10_rate, 2)
    item11_amount = round(box_count * item11_rate, 2)
    item13_amount = round(open_top_count * item13_rate, 2)
    item18_amount = round(box_count * item18_rate, 2)
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
            ROUTE,
            batch["id"],
            batch["ship_name"],
            "三三〇处专用线",
            "三三〇处专用线",
            event_date,
            event_date,
            event_date,
            wagon_count,
            box_count,
            "confirmed_weight",
            confirmed_weight,
            "container_batch",
            f"{batch['ship_name']}|{LOT}|route_a",
            "cost_generated",
            "system_generated",
            source_ref,
            "codex",
            "codex",
            now,
            now,
            f"ydid_count={ydid_count};trip_box_count={box_count};open_top_count={open_top_count};"
            f"top_open_count={top_open_count};railway_weight={railway_weight};"
            f"missing_lines={','.join(missing_lines) if missing_lines else ''}",
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
                float(m.get("marked_weight") or 0.0),
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
        terms=terms,
        box_trip_count=box_count,
        confirmed_weight=confirmed_weight,
        railway_weight=railway_weight,
        freight_sum_yuan=freight_sum,
        metro_amount=metro_amount,
        wagon_occupancy_amount=wagon_occupancy_amount,
        transfer_amount=transfer_amount,
        tarpaulin_amount=tarpaulin_amount,
        item9_amount=item9_amount,
        item10_amount=item10_amount,
        item11_amount=item11_amount,
        item13_amount=item13_amount,
        item13_box_count=open_top_count,
        item18_amount=item18_amount,
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
                "container_batch",
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
        "confirmed_weight": confirmed_weight,
        "railway_weight": railway_weight,
        "freight_sum": freight_sum,
        "metro_amount": metro_amount,
        "open_top_count": open_top_count,
        "top_open_count": top_open_count,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    now = now_iso_beijing()
    conn = _open()
    try:
        item_ids = _item_ids(conn)
        batches = _release_batches(conn, args.ship)
        outputs = []
        for batch in batches:
            out = _upsert_batch(conn, batch=batch, item_ids=item_ids, now=now)
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
                f"box_trips={o['box_count']} confirmed_weight={o['confirmed_weight']} "
                f"railway_weight={o['railway_weight']} freight={o['freight_sum']} "
                f"metro={o['metro_amount']} open_top={o['open_top_count']} "
                f"top_open={o['top_open_count']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
