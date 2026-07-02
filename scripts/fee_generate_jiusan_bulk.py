#!/usr/bin/env python3
"""九三大豆 route C 散粮费用生成 + 现场确认单落地。

按 bulk_loading_notice_wagon 的 notice_date + ship_name + track + lot 分组，生成：
  - fee_item_catalog 种子
  - fee_batch
  - fee_batch_member
  - fee_record
  - doc_template
  - doc_instance(.docx)

用法:
  .venv/bin/python scripts/fee_generate_jiusan_bulk.py
  .venv/bin/python scripts/fee_generate_jiusan_bulk.py --ship 诚信 --date 2026-06-26 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402
from sop_hub.fees.jiusan_bulk import (  # noqa: E402
    allocated_confirmed_weight,
    build_docx_path,
    billing_weight,
    calc_fee_items,
    freight_fee_yuan,
    load_contract_terms,
    load_line_rates,
    load_weight_confirmation,
    load_route_c_config,
    render_onsite_confirm_docx,
    resolve_line_rate,
    stable_hash,
)
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

DB = REPO / "data" / "sop_agent.db"
YAML_PATH = REPO / "config" / "project_sops" / "jiusan.yaml"
PROJECT = "jiusan"
LOT = "lot02"
ROUTE = "C"
PROJECT_NAME = "九三大豆铁运项目(散粮车)"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _load_yaml() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}


REQUIRED_CODES = [
    "route_c_income",
    "nrf_cost",
    "route_c_pickup_fee",
    "metro_fee",
    "route_c_wagon_occupancy",
    "track_scale",
    "aux_bulk_loading",
    "aux_bulk_inspection",
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


def _ensure_doc_template(conn: sqlite3.Connection, config: dict, now: str) -> str:
    tpl = (config.get("doc_templates") or {}).get("onsite_confirm_sheet") or {}
    tid = stable_hash(PROJECT, "doc_template", tpl.get("template_code", "jiusan_bulk_onsite_confirm"))
    conn.execute(
        """INSERT OR REPLACE INTO doc_template
           (id, template_code, template_name, doc_type, project_scope, applicable_fee_code,
            output_format, template_path, enabled, created_at, updated_at, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tid,
            tpl.get("template_code", "jiusan_bulk_onsite_confirm"),
            tpl.get("template_name", "锦州港装卸辅助作业现场确认单"),
            "onsite_confirm_sheet",
            PROJECT,
            ",".join(tpl.get("applicable_fee_codes") or []),
            tpl.get("output_format", "docx"),
            "",
            1,
            now,
            now,
            "auto-seeded by fee_generate_jiusan_bulk.py",
        ),
    )
    return tid


def _groups(conn: sqlite3.Connection, ship: str, date: str) -> list[sqlite3.Row]:
    sql = """
        SELECT notice_date, track, ship_name, lot, total_cars, COUNT(*) AS cars
        FROM bulk_loading_notice_wagon
        WHERE project=? AND lot=?
    """
    params: list = [PROJECT, LOT]
    if ship:
        sql += " AND ship_name=?"
        params.append(ship)
    if date:
        sql += " AND notice_date=?"
        params.append(date)
    sql += """
        GROUP BY notice_date, track, ship_name, lot, total_cars
        ORDER BY notice_date, ship_name, track
    """
    return conn.execute(sql, tuple(params)).fetchall()


def _release_batch_id(conn: sqlite3.Connection, ship_name: str) -> str:
    row = conn.execute(
        "SELECT id FROM release_batches WHERE project=? AND ship_name=? AND batch_sequence=?",
        (PROJECT, ship_name, LOT),
    ).fetchone()
    return row["id"] if row else ""


def _members(conn: sqlite3.Connection, ship_name: str, notice_date: str, track: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT bln.id AS notice_id, bln.notice_date, bln.track, bln.ship_name, bln.total_cars,
               bln.car_seq, bln.car_no, bln.car_model, bln.ydid,
               ws.id AS wagon_shipment_id, ws.batch_id, ws.ticketed_at, ws.departed_at,
               ws.arrived_at, ws.delivered_at, ws.marked_weight, ws.computed_loading_weight,
               ws.freight_fee
        FROM bulk_loading_notice_wagon bln
        LEFT JOIN wagon_shipments ws ON ws.ydid = bln.ydid
        WHERE bln.project=? AND bln.ship_name=? AND bln.notice_date=? AND bln.track=? AND bln.lot=?
        ORDER BY bln.car_seq, bln.car_no
        """,
        (PROJECT, ship_name, notice_date, track, LOT),
    ).fetchall()
    return [dict(r) for r in rows]


def _ship_marked_weight(conn: sqlite3.Connection, release_batch_id: str) -> float:
    row = conn.execute(
        "SELECT round(sum(coalesce(marked_weight,0)),2) AS total_weight FROM wagon_shipments WHERE batch_id=?",
        (release_batch_id,),
    ).fetchone()
    return float((row["total_weight"] if row else 0.0) or 0.0)


def _upsert_group(
    conn: sqlite3.Connection,
    *,
    item_ids: dict[str, str],
    template_id: str,
    notice_date: str,
    track: str,
    ship_name: str,
    total_cars: int,
    members: list[dict],
    now: str,
    write_docs: bool,
) -> dict:
    terms = load_contract_terms(conn, PROJECT, ROUTE)
    line_rates = load_line_rates(conn, PROJECT, ROUTE, "metro_fee")
    release_batch_id = _release_batch_id(conn, ship_name)
    batch_id = stable_hash(PROJECT, "bulk_fee_batch", ship_name, notice_date, track, LOT)
    car_count = len(members)
    total_weight = round(sum(billing_weight(m) for m in members), 2)
    freight_sum = round(sum(freight_fee_yuan(m.get("freight_fee")) for m in members), 2)
    ship_total_marked_weight = _ship_marked_weight(conn, release_batch_id)
    confirmed_total_weight = load_weight_confirmation(conn, release_batch_id, ROUTE)
    confirmed_batch_weight = allocated_confirmed_weight(confirmed_total_weight, total_weight, ship_total_marked_weight)
    metro_default = float((terms.get("metro_fee") or {}).get("default_rate") or 3.75)
    line_rate, resolved_line = resolve_line_rate(track if track and track.endswith("道") else None, line_rates, metro_default)
    source_ref = f"bulk_loading_notice_wagon:{ship_name}:{notice_date}:{track}:{LOT}"
    wagon_occupancy_rate = float((terms.get("route_c_wagon_occupancy") or {}).get("default_rate") or 0.64)
    pickup_rate = float((terms.get("route_c_pickup_fee") or {}).get("default_rate") or 0.0)

    conn.execute("DELETE FROM fee_batch_member WHERE fee_batch_id=?", (batch_id,))
    conn.execute("DELETE FROM fee_record WHERE fee_batch_id=?", (batch_id,))
    doc_ids = [r["id"] for r in conn.execute("SELECT id FROM doc_instance WHERE fee_batch_id=?", (batch_id,)).fetchall()]
    for did in doc_ids:
        conn.execute("DELETE FROM doc_instance_signoff WHERE doc_instance_id=?", (did,))
    conn.execute("DELETE FROM doc_instance WHERE fee_batch_id=?", (batch_id,))

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
            "dispatch_train",
            "bulk",
            ROUTE,
            release_batch_id,
            ship_name,
            "九三集团铁岭大豆科技有限公司专用线",
            track,
            notice_date,
            notice_date,
            notice_date,
            car_count,
            0,
            "confirmed_weight_allocated",
            confirmed_batch_weight,
            "train",
            f"{ship_name}|{notice_date}|{track}|{LOT}",
            "cost_generated",
            "system_generated",
            source_ref,
            "codex",
            "codex",
            now,
            now,
            f"notice_total_cars={total_cars};marked_weight={total_weight};ship_total_marked_weight={ship_total_marked_weight};resolved_line={resolved_line or '<default>'}",
        ),
    )

    car_nos: list[str] = []
    for m in members:
        mid = stable_hash(batch_id, m.get("car_no"), m.get("ydid"))
        car_nos.append(str(m.get("car_no") or ""))
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
                "wagon",
                m.get("wagon_shipment_id"),
                None,
                m.get("ydid"),
                m.get("car_no"),
                None,
                m.get("marked_weight"),
                None,
                billing_weight(m),
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
                f"track={track}",
            ),
        )

    fee_items = calc_fee_items(
        terms=terms,
        total_weight=total_weight,
        freight_sum_yuan=freight_sum,
        car_count=car_count,
        line_rate=line_rate,
        code_weights={"route_c_income": confirmed_batch_weight},
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
                "train",
                f"{ship_name}|{notice_date}|{track}|{LOT}",
                item.document_flow_type,
                "generated",
                None,
                "system_generated",
                source_ref,
                1,
                "codex",
                "codex",
                now,
                now,
                item.service_no and f"service_no={item.service_no}" or "",
            ),
        )

    doc_path = build_docx_path(notice_date, ship_name, track, car_count)
    if write_docs:
        try:
            render_onsite_confirm_docx(
                path=doc_path,
                notice_date=notice_date,
                project_name=PROJECT_NAME,
                track=track,
                car_count=car_count,
                car_nos=car_nos,
                fee_items=fee_items,
                ship_name=ship_name,
            )
        except ModuleNotFoundError:
            pass
    doc_id = stable_hash(batch_id, "doc_instance", "onsite_confirm_sheet")
    conn.execute(
        """INSERT OR REPLACE INTO doc_instance
           (id, template_id, project_id, fee_batch_id, fee_record_id, doc_type, doc_status,
            file_path_docx, file_path_pdf, version_no, generated_at, source_mode, source_ref,
            created_by, updated_by, created_at, updated_at, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            doc_id,
            template_id,
            PROJECT,
            batch_id,
            None,
            "onsite_confirm_sheet",
            "generated",
            str(doc_path),
            None,
            1,
            now,
            "system_generated",
            source_ref,
            "codex",
            "codex",
            now,
            now,
            "auto-generated from bulk_loading_notice_wagon",
        ),
    )
    return {
        "batch_id": batch_id,
        "doc_path": str(doc_path),
        "car_count": car_count,
        "marked_weight": total_weight,
        "confirmed_weight": confirmed_batch_weight,
        "freight_sum": freight_sum,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    now = now_iso_beijing()
    cfg = load_route_c_config()
    if not cfg:
        raise SystemExit("jiusan.yaml 缺 cost_structure.route_c_bulk")

    conn = _open()
    try:
        item_ids = _item_ids(conn)
        template_id = _ensure_doc_template(conn, cfg, now)
        groups = _groups(conn, args.ship, args.date)
        if not groups:
            print("未找到匹配的九三散粮通知台账分组")
            conn.rollback()
            return

        outputs = []
        for g in groups:
            members = _members(conn, g["ship_name"], g["notice_date"], g["track"])
            if not members:
                continue
            outputs.append(
                _upsert_group(
                    conn,
                    item_ids=item_ids,
                    template_id=template_id,
                    notice_date=g["notice_date"],
                    track=g["track"],
                    ship_name=g["ship_name"],
                    total_cars=int(g["total_cars"] or g["cars"] or len(members)),
                    members=members,
                    now=now,
                    write_docs=args.apply,
                )
            )

        if args.apply:
            conn.commit()
            print(f"COMMIT ✓ 生成 {len(outputs)} 个九三散粮费用批次")
            for o in outputs:
                print(
                    f"  batch={o['batch_id']} cars={o['car_count']} "
                    f"marked_weight={o['marked_weight']} confirmed_weight={o['confirmed_weight']} "
                    f"freight={o['freight_sum']} doc={o['doc_path']}"
                )
        else:
            conn.rollback()
            print(f"DRY-RUN: 将生成 {len(outputs)} 个九三散粮费用批次")
            for o in outputs:
                print(
                    f"  batch={o['batch_id']} cars={o['car_count']} "
                    f"marked_weight={o['marked_weight']} confirmed_weight={o['confirmed_weight']} "
                    f"freight={o['freight_sum']} doc={o['doc_path']}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
