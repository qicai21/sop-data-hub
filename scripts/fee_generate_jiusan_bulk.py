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
    build_docx_path,
    billing_weight,
    calc_fee_items,
    freight_fee_yuan,
    load_route_c_config,
    render_onsite_confirm_docx,
    stable_hash,
)
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

DB = REPO / "data" / "sop_agent.db"
YAML_PATH = REPO / "config" / "project_sops" / "jiusan.yaml"
PROJECT = "jiusan"
LOT = "lot02"
PROJECT_NAME = "九三大豆铁运项目(散粮车)"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _load_yaml() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}


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
                "ton" if item.get("base") == "railway_billing_weight" else "car" if item.get("base") == "car_count" else "batch",
                item.get("rate"),
                item.get("tax"),
                item.get("doc_type") or "",
                "JGWL-JZTS-DD-202601",
                str(YAML_PATH),
                1,
                "system_generated",
                "jiusan.yaml:cost_structure.route_c_bulk",
                "codex",
                "codex",
                now,
                now,
                item.get("service_no") and f"service_no={item['service_no']}" or "",
            ),
        )
    return ids


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


def _upsert_group(
    conn: sqlite3.Connection,
    *,
    cfg: dict,
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
    release_batch_id = _release_batch_id(conn, ship_name)
    batch_id = stable_hash(PROJECT, "bulk_fee_batch", ship_name, notice_date, track, LOT)
    car_count = len(members)
    total_weight = round(sum(billing_weight(m) for m in members), 2)
    freight_sum = round(sum(freight_fee_yuan(m.get("freight_fee")) for m in members), 2)
    source_ref = f"bulk_loading_notice_wagon:{ship_name}:{notice_date}:{track}:{LOT}"

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
            cfg.get("route_code", "C"),
            release_batch_id,
            ship_name,
            "九三集团铁岭大豆科技有限公司专用线",
            track,
            notice_date,
            notice_date,
            notice_date,
            car_count,
            0,
            "billing_weight",
            total_weight,
            cfg.get("recognition_scope", "train"),
            f"{ship_name}|{notice_date}|{track}|{LOT}",
            "cost_generated",
            "system_generated",
            source_ref,
            "codex",
            "codex",
            now,
            now,
            f"notice_total_cars={total_cars}",
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
        config=cfg,
        total_weight=total_weight,
        freight_sum_yuan=freight_sum,
        car_count=car_count,
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
                cfg.get("recognition_scope", "train"),
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
        "total_weight": total_weight,
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
        item_ids = _ensure_catalog(conn, cfg, now)
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
                    cfg=cfg,
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
                    f"weight={o['total_weight']} freight={o['freight_sum']} doc={o['doc_path']}"
                )
        else:
            conn.rollback()
            print(f"DRY-RUN: 将生成 {len(outputs)} 个九三散粮费用批次")
            for o in outputs:
                print(
                    f"  batch={o['batch_id']} cars={o['car_count']} "
                    f"weight={o['total_weight']} freight={o['freight_sum']} doc={o['doc_path']}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
