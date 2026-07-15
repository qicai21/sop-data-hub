#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"

JIUSAN_CONTRACT_PATH = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/业务项目档案/P008_大豆/01_有效合同/主合同/JGWL-JZTS-DD-202601_物流发展-铁晟2026大豆合同.docx"
)
GAOTIAN_CONTRACT_PATH = (
    Path.home()
    / "Documents/contracts_2025_2026/202601280004_高天机车牵引合同_德鑫__高天地铁费合同_不包含4_5_6道/附件2_机车牵引配合服务合同.pdf_高天地铁费合同_不包含4.5.6道_.pdf_高天地铁费合同_四五六道.pdf"
)
SUMMARY_XLSX = (
    Path.home()
    / "Library/Mobile Documents/com~apple~CloudDocs/业务项目档案/_汇总分析与方案材料/业务项目汇总表.xlsx"
)

PROJECT = "jiusan"
CONTRACT_REF_MAIN = "JGWL-JZTS-DD-202601"
CONTRACT_REF_GAOTIAN = "GAOTIAN-ANNEX2-20260128"
CONTRACT_REF_CHENGXIN = "20250103a"
CONTRACT_REF_TRACK = "TRACK-SCALE-JZ-2026"
NOW_DATE = "2026-07-02"
EFFECTIVE_FROM = "2026-01-01"


def h(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def upsert_fee_item_catalog(conn: sqlite3.Connection) -> None:
    rows = [
        ("route_a_metro_fee", "路线A地铁费", "cost", "ton", 3.75, 0.09, CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "manual_seed:2026-07-15"),
        ("route_a_wagon_occupancy", "路线A货车占用费", "cost", "ton", 0.64, 0.09, CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "manual_seed:r82"),
        ("route_a_tarpaulin", "路线A篷布租金", "cost", "box", 12.8, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:r82"),
        ("route_a_item9", "路线A第9项清理整备服务（扫箱、清箱）", "cost", "box", 40.0, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:2026-07-15"),
        ("route_a_item10", "路线A第10项洗箱服务", "cost", "box", 100.0, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:2026-07-15"),
        ("route_a_item11", "路线A第11项粮食专用箱专项检查", "cost", "box", 80.0, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:2026-07-15"),
        ("route_a_item13", "路线A第13项篷布专项检查服务", "cost", "box", 15.0, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:2026-07-15"),
        ("route_a_item18", "路线A第18项箱门捆扎施封", "cost", "box", 15.0, 0.06, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:2026-07-15"),
        ("route_c_pickup_fee", "路线C取送车费", "cost", "car", 32.4, 0.09, CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "manual_seed:r82"),
        ("route_c_wagon_occupancy", "路线C货车占用费", "cost", "ton", 0.64, 0.09, CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "manual_seed:r82"),
    ]
    for code, name, side, unit, rate, tax, cref, cpath, src in rows:
        conn.execute(
            """INSERT OR REPLACE INTO fee_item_catalog
               (id, project_id, fee_code, fee_name, charge_side, pricing_unit,
                default_rate, tax_rate, document_flow_type, contract_ref,
                contract_path, enabled, source_mode, source_ref, created_by,
                updated_by, created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                h(PROJECT, "fee_item", code),
                PROJECT,
                code,
                name,
                side,
                unit,
                rate,
                tax,
                "",
                cref,
                cpath,
                1,
                "manual_entry",
                src,
                "codex",
                "codex",
                NOW_DATE,
                NOW_DATE,
                "",
            ),
        )


def upsert_contract_fee_terms(conn: sqlite3.Connection) -> None:
    terms = [
        # route A
        ("A", "route_a_income", "路线A运输收入", "income", "confirmed_weight", "ton", 65.43, None, "锦州港物流发展有限公司", "", CONTRACT_REF_MAIN, str(JIUSAN_CONTRACT_PATH), "sheet=P008_大豆_路线A_三三零", "route A 收入拆分:37.18@9% + 28.25@6%; 当前先按总价落"),
        ("A", "route_a_nrf_cost", "路线A国铁运费", "cost", "freight_fee_sum", "batch", None, 0.09, "", "中国铁路", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零", "票面国铁费取 wagon_container_shipments.freight_fee 按 ydid 汇总"),
        ("A", "route_a_metro_fee", "路线A地铁费", "cost", "marked_weight_x_line_rate", "ton", 3.75, 0.09, "", "锦州高天铁路有限责任公司", CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "第四条(一)(二)", "按作业道线价格 * 标载; 缺少道线时默认 3.75"),
        ("A", "route_a_wagon_occupancy", "路线A货车占用费", "cost", "marked_weight_tiered", "ton", 0.64, 0.09, "", "锦州高天铁路有限责任公司", CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "第四条(一)(二)", "疏港普通货物 <300万吨 0.64元/吨, >=300万吨 0.60元/吨"),
        ("A", "route_a_transfer_fee", "路线A诚信倒运费", "cost", "box_trip_count", "box", 444.0, 0.09, "", "诚信集装箱储运", CONTRACT_REF_CHENGXIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零", "三三零 444元/箱; 汇总表备注合同已过期待续签"),
        ("A", "route_a_tarpaulin", "路线A篷布租金", "cost", "formula", "box", 12.8, 0.06, "", "锦州港物流发展有限公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零", "12.8元/箱 * 70%租用比例; 约30%国铁免费篷布"),
        ("A", "route_a_item9", "路线A第9项清理整备服务（扫箱、清箱）", "cost", "box_trip_count", "box", 40.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零!A31:C39", "全部集装箱箱次"),
        ("A", "route_a_item10", "路线A第10项洗箱服务", "cost", "box_trip_count", "box", 100.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零!A31:C39", "全部集装箱箱次"),
        ("A", "route_a_item11", "路线A第11项粮食专用箱专项检查", "cost", "box_trip_count", "box", 80.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零!A31:C39", "全部集装箱箱次"),
        ("A", "route_a_item13", "路线A第13项篷布专项检查服务", "cost", "open_top_box_trip_count", "box", 15.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零!A31:C39", "仅限敞顶箱"),
        ("A", "route_a_item18", "路线A第18项箱门捆扎施封", "cost", "box_trip_count", "box", 15.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线A_三三零!A31:C39", "全部集装箱箱次"),
        # route C
        ("C", "route_c_income", "路线C运输收入", "income", "confirmed_weight", "ton", 65.13, None, "锦州港物流发展有限公司", "", CONTRACT_REF_MAIN, str(JIUSAN_CONTRACT_PATH), "sheet=P008_大豆_路线C_新台子", "route C 收入拆分:50.07@9% + 15.06@6%; 当前先按总价落"),
        ("C", "nrf_cost", "国铁运费", "cost", "freight_fee_sum", "batch", None, 0.09, "", "中国铁路", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线C_新台子", "票面国铁费从 wagon_shipments.freight_fee 汇总"),
        ("C", "route_c_pickup_fee", "路线C取送车费", "cost", "car_count", "car", 32.4, 0.09, "", "中国铁路", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线C_新台子", "32.40元/车"),
        ("C", "metro_fee", "地铁费", "cost", "marked_weight_x_line_rate", "ton", 3.75, 0.09, "", "锦州高天铁路有限责任公司", CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "第四条(一)(二)", "按作业道线价格 * 标载; 缺少道线时默认 3.75"),
        ("C", "route_c_wagon_occupancy", "路线C货车占用费", "cost", "marked_weight_tiered", "ton", 0.64, 0.09, "", "锦州高天铁路有限责任公司", CONTRACT_REF_GAOTIAN, str(GAOTIAN_CONTRACT_PATH), "第四条(一)(二)", "疏港普通货物 <300万吨 0.64元/吨, >=300万吨 0.60元/吨"),
        ("C", "track_scale", "轨道衡费", "cost", "marked_weight", "ton", 0.7, 0.0, "", "大连中铁外服国际货运代理锦州分公司", CONTRACT_REF_TRACK, str(SUMMARY_XLSX), "sheet=P008_大豆_路线C_新台子", "免税"),
        ("C", "aux_bulk_loading", "散粮车装卸辅助作业服务", "cost", "car_count", "car", 450.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线C_新台子", "第19项"),
        ("C", "aux_bulk_inspection", "散粮车车体检查及作业现场检查服务", "cost", "car_count", "car", 200.0, 0.06, "", "二级公司", CONTRACT_REF_MAIN, str(SUMMARY_XLSX), "sheet=P008_大豆_路线C_新台子", "第20项"),
    ]
    for route, code, name, side, basis, unit, rate, tax, cp, sp, cref, epath, eloc, note in terms:
        conn.execute(
            """INSERT OR REPLACE INTO contract_fee_terms
               (id, contract_ref, project_id, route_code, fee_code, fee_name,
                charge_side, pricing_basis, pricing_unit, default_rate, currency,
                tax_rate, counterparty, settle_party, evidence_path, evidence_locator,
                effective_from, effective_to, enabled, source_mode, source_ref,
                created_by, updated_by, created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                h("term", PROJECT, route, code),
                cref,
                PROJECT,
                route,
                code,
                name,
                side,
                basis,
                unit,
                rate,
                "CNY",
                tax,
                cp,
                sp,
                epath,
                eloc,
                EFFECTIVE_FROM,
                None,
                1,
                "manual_entry",
                "seed_jiusan_contract_terms.py",
                "codex",
                "codex",
                NOW_DATE,
                NOW_DATE,
                note,
            ),
        )


def upsert_contract_line_rates(conn: sqlite3.Connection) -> None:
    line_groups = [
        (("煤一", "煤二", "煤三", "煤四", "煤五", "煤六"), 4.5),
        (("七道", "八道", "九道", "十道"), 3.75),
        (("十四道", "十五道"), 3.5),
    ]
    for route, fee_code in (("A", "route_a_metro_fee"), ("C", "metro_fee")):
        term_id = h("term", PROJECT, route, fee_code)
        for names, rate in line_groups:
            for line_name in names:
                conn.execute(
                    """INSERT OR REPLACE INTO contract_line_rates
                       (id, contract_fee_term_id, contract_ref, project_id, route_code,
                        fee_code, line_name, rate, pricing_unit, tax_rate, evidence_path,
                        evidence_locator, effective_from, effective_to, enabled, source_mode,
                        source_ref, created_by, updated_by, created_at, updated_at, note)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        h("line", PROJECT, route, fee_code, line_name),
                        term_id,
                        CONTRACT_REF_GAOTIAN,
                        PROJECT,
                        route,
                        fee_code,
                        line_name,
                        rate,
                        "ton",
                        0.09,
                        str(GAOTIAN_CONTRACT_PATH),
                        "第四条(一)(二)",
                        EFFECTIVE_FROM,
                        None,
                        1,
                        "manual_entry",
                        "seed_jiusan_contract_terms.py",
                        "codex",
                        "codex",
                        NOW_DATE,
                        NOW_DATE,
                        "",
                    ),
                )


def upsert_weight_confirmations(conn: sqlite3.Connection) -> None:
    batch_rows = {
        (r[1], r[2]): r[0]
        for r in conn.execute(
            "SELECT id, ship_name, batch_sequence FROM release_batches WHERE project=? AND ship_name='和谐1'",
            (PROJECT,),
        ).fetchall()
    }
    rows = [
        ("和谐1", "lot01", "A", "port_weighing_departure", 47951.06, "2026-06-27", "用户确认", "港口上货过磅重量"),
        ("和谐1", "lot02", "C", "port_weighing_departure", 20112.7, "2026-06-27", "用户确认", "港口上货过磅重量"),
    ]
    for ship, lot, route, wtype, weight, cdate, by, note in rows:
        batch_id = batch_rows[(ship, lot)]
        conn.execute(
            """INSERT OR REPLACE INTO shipment_weight_confirmation
               (id, project_id, ship_name, release_batch_id, route_code, weight_type,
                confirmed_weight, unit, confirmed_date, confirmed_by, evidence_path,
                evidence_locator, source_mode, source_ref, created_by, updated_by,
                created_at, updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                h("weight", PROJECT, ship, lot, route, wtype),
                PROJECT,
                ship,
                batch_id,
                route,
                wtype,
                weight,
                "ton",
                cdate,
                by,
                "",
                "",
                "manual_entry",
                "user_confirmed:2026-07-02",
                "codex",
                "codex",
                NOW_DATE,
                NOW_DATE,
                note,
            ),
        )


def update_contract_doc_path(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE contracts SET doc_path=?, updated_at=? WHERE id='jiusan_soybean_logistics_service_202601'",
        (str(JIUSAN_CONTRACT_PATH), NOW_DATE),
    )


def main() -> None:
    conn = sqlite3.connect(str(DB))
    try:
        upsert_fee_item_catalog(conn)
        upsert_contract_fee_terms(conn)
        upsert_contract_line_rates(conn)
        upsert_weight_confirmations(conn)
        update_contract_doc_path(conn)
        conn.commit()
        print("APPLIED: jiusan contract terms, line rates, weight confirmations")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
