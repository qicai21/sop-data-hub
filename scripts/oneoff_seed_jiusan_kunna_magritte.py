"""一次性脚本:为九三大豆昆娜+玛格丽特建 4 个 release_batch + wagon 行。

manifest 数据见 data/contracts/jiusan_soybean/dispatch_records/
对账参考 /tmp/jiusan_matches_v2.json(已 100% 命中 95306)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data/sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")
MATCHES = Path("/tmp/jiusan_matches_v2.json")
MANIFEST_PARSED = Path("/tmp/jiusan_manifest_parsed.json")


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def excel_iso(n) -> str | None:
    if isinstance(n, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
    if hasattr(n, "isoformat"):
        return n.isoformat()
    return None


def build_batches(now: str) -> list[dict]:
    """Return 4 release_batches rows ready for insert."""
    common = {
        "project": "jiusan",
        "contract_no": "JGWL-JZTS-DD-202601",
        "cargo_name": "大豆",
        "consignor": "锦州港物流发展有限公司",
        "origin_station": "高桥镇",
        "trade_type": "外贸进口",
    }
    rows = [
        # (ship, lot, transport_mode, dest_line, consignee, qty_tons, car_count,
        #  notice_date, first_date, last_date, cargo_product_name)
        ("昆娜", "lot01", "铁路集装箱", "国家粮食和物资储备局辽宁局三三0处专用线",
         "国家粮食和物资储备局辽宁局三三0处", 38069.62, 663,
         "2026-04-29", "2026-04-29", "2026-05-13", "大豆-集装箱"),
        ("昆娜", "lot02", "铁路散粮车", "九三集团铁岭大豆科技有限公司专用线",
         "九三集团铁岭大豆科技有限公司", 30501.66, 476,
         "2026-04-30", "2026-04-30", "2026-05-19", "大豆-散粮车"),
        ("玛格丽特", "lot01", "铁路集装箱", "国家粮食和物资储备局辽宁局三三0处专用线",
         "国家粮食和物资储备局辽宁局三三0处", 48516.86, 861,
         "2026-05-18", "2026-05-18", "2026-06-03", "大豆-集装箱"),
        ("玛格丽特", "lot02", "铁路散粮车", "九三集团铁岭大豆科技有限公司专用线",
         "九三集团铁岭大豆科技有限公司", 20292.78, 320,
         "2026-05-19", "2026-05-19", "2026-05-30", "大豆-散粮车"),
    ]
    out = []
    for ship, lot, mode, destline, consignee, qty, cars, notice_d, first_d, last_d, prod_name in rows:
        batch_key = f"jiusan|{ship}|{lot}|{first_d}"
        bid = stable_hash("jiusan", ship, lot, first_d)
        src = {"ship": ship, "lot": lot, "src": "manifest_seed_20260610",
               "manifest_total_weight": qty, "manifest_car_count": cars,
               "first_event_date": first_d, "last_event_date": last_d}
        out.append({
            **common,
            "id": bid, "batch_key": batch_key, "ship_name": ship,
            "batch_sequence": lot, "transport_mode": mode,
            "destination_station": "新台子",
            "yard_location": destline,
            "consignee": consignee,
            "notice_date": notice_d, "batch_date": first_d,
            "batch_quantity": qty, "total_planned_quantity": qty,
            "remaining_quantity": 0.0,
            "batch_count": cars, "actual_wagon_count": cars,
            "shipped_weight_tons": qty, "remaining_weight_tons": 0.0,
            "dispatch_status": "confirmed_received",
            "dispatch_status_note": "manifest_seed_full_delivered",
            "dispatch_status_updated_at": now,
            "confirmed_received_at": last_d + "T20:00:00+08:00",
            "cargo_product_name": prod_name,
            "cargo_name_detail": prod_name,
            "source_file_name": f"{ship}{'集装箱' if 'lot01' == lot else '散粮车'}明细.xlsx",
            "source_json": json.dumps(src, ensure_ascii=False),
            "searchable_text": f"九三大豆 {ship} {lot} {mode} 高桥镇 新台子 {destline}",
            "created_at": now, "updated_at": now,
        })
    return out


def insert_batches(conn, rows):
    cols = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.cursor()
    for r in rows:
        cur.execute(
            f"INSERT OR REPLACE INTO release_batches ({','.join(cols)}) VALUES ({placeholders})",
            [r[c] for c in cols],
        )
    return len(rows)


def insert_container_shipments(conn, rail_conn, kc_matches, mc_matches,
                               kunna_id, magritte_id, manifest_data, now):
    """集装箱:每 ydid 2 box → wagon_container_shipments 2 行。"""
    # 用 manifest 把 (car_no, sheet_date) -> [box_no...] 收齐
    def collect_boxes(cars_rows):
        out = defaultdict(list)
        for c in cars_rows:
            d_ = c.get("sheet_date") or ""
            if not d_:
                continue
            key = (c["car_no"], d_[:10])
            if c.get("box_no"):
                out[key].append(c["box_no"])
        return out

    kc_boxes = collect_boxes(manifest_data["kunna_container_cars"])
    mc_boxes = collect_boxes(manifest_data["magritte_container_cars"])

    rail_cur = rail_conn.cursor()
    sop_cur = conn.cursor()

    n_total = 0
    for matches, boxes_map, batch_id, ship in [
        (kc_matches, kc_boxes, kunna_id, "昆娜"),
        (mc_matches, mc_boxes, magritte_id, "玛格丽特"),
    ]:
        ydids = [m[2] for m in matches]
        chunks = [ydids[i:i+500] for i in range(0, len(ydids), 500)]
        rows95 = {}
        for ch in chunks:
            ph = ",".join("?"*len(ch))
            rail_cur.execute(
                f"SELECT ydid, czydid, car_no, container_no_raw, marked_weight, "
                f"transport_mode_code, transport_mode_name, "
                f"accepted_at, loaded_at, ticketed_at, departed_at, arrived_at, delivered_at, "
                f"status_name, latest_stage_key, latest_stage_name, latest_event_time, "
                f"origin_name, destination_name, cargo_name "
                f"FROM shipments WHERE ydid IN ({ph})", ch)
            for r in rail_cur.fetchall():
                rows95[r[0]] = r

        for cn, d_str, ydid, tk_date, container_raw in matches:
            r95 = rows95.get(ydid)
            if not r95:
                continue
            boxes = boxes_map.get((cn, d_str), [])
            # 兜底:从 95306 container_no_raw 拆
            if not boxes and r95[3]:
                box_chunks = []
                for token in r95[3].split("/"):
                    digits = "".join(c for c in token if c.isdigit())
                    if digits:
                        box_chunks.append(digits[-7:])
                boxes = box_chunks
            for pos, box_no in enumerate(boxes, 1):
                row_id = stable_hash(cn, str(box_no), ydid)
                sop_cur.execute("""
                    INSERT OR REPLACE INTO wagon_container_shipments (
                        id, car_no, box_no, box_position, ydid, czydid, batch_id,
                        car_model, ticketed_at, departed_at, arrived_at, delivered_at,
                        accepted_at, loaded_at, status_name, latest_stage_key,
                        latest_stage_name, latest_event_time, origin_name,
                        destination_name, transport_mode_code, transport_mode_name,
                        cargo_name, marked_weight, project_id, ship_name, consignor,
                        consignee, dispatch_status, source_message_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row_id, cn, str(box_no), pos, ydid, r95[1], batch_id,
                    None,  # car_model 集装箱不存
                    r95[9], r95[10], r95[11], r95[12],
                    r95[7], r95[8], r95[13], r95[14],
                    r95[15], r95[16], r95[17],
                    r95[18], r95[5], r95[6],
                    r95[19], r95[4],
                    "jiusan", ship, "锦州港物流发展有限公司",
                    "国家粮食和物资储备局辽宁局三三0处",
                    "confirmed_received", "manifest_seed_20260610",
                    now, now,
                ))
                n_total += 1
    return n_total


def insert_bulk_shipments(conn, rail_conn, kb_matches, mb_matches,
                          kunna_id, magritte_id, now):
    """散粮车:每 ydid 1 row → wagon_shipments。"""
    rail_cur = rail_conn.cursor()
    sop_cur = conn.cursor()
    n_total = 0
    for matches, batch_id, ship in [
        (kb_matches, kunna_id, "昆娜"),
        (mb_matches, magritte_id, "玛格丽特"),
    ]:
        ydids = [m[2] for m in matches]
        chunks = [ydids[i:i+500] for i in range(0, len(ydids), 500)]
        rows95 = {}
        for ch in chunks:
            ph = ",".join("?"*len(ch))
            rail_cur.execute(
                f"SELECT ydid, czydid, car_no, car_model, cargo_name, shipper_name, "
                f"consignee_name, origin_name, destination_name, "
                f"accepted_at, loaded_at, ticketed_at, departed_at, arrived_at, "
                f"delivered_at, status_name, latest_stage_key, latest_stage_name, "
                f"latest_event_time, marked_weight, freight_fee, "
                f"transport_mode_code, transport_mode_name "
                f"FROM shipments WHERE ydid IN ({ph})", ch)
            for r in rail_cur.fetchall():
                rows95[r[0]] = r
        for cn, d_str, ydid, tk_date in matches:
            r = rows95.get(ydid)
            if not r:
                continue
            row_id = stable_hash("bulk", ydid)
            sop_cur.execute("""
                INSERT OR REPLACE INTO wagon_shipments (
                    id, batch_id, car_no, car_model, cargo_name, shipper_name,
                    consignee_name, origin_name, destination_name,
                    accepted_at, loaded_at, ticketed_at, departed_at, arrived_at,
                    delivered_at, status_name, latest_stage_key, latest_stage_name,
                    latest_event_time, marked_weight, freight_fee, ydid, czydid,
                    transport_mode_code, transport_mode_name,
                    project_id, ship_name, dispatch_status, source_message_id,
                    confirmed_received_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row_id, batch_id, r[2], r[3], r[4], r[5],
                r[6], r[7], r[8],
                r[9], r[10], r[11], r[12], r[13],
                r[14], r[15], r[16], r[17],
                r[18], r[19], r[20], ydid, r[1],
                r[21], r[22],
                "jiusan", ship, "confirmed_received", "manifest_seed_20260610",
                r[14], now, now,
            ))
            n_total += 1
    return n_total


def main(apply: bool = False):
    now = now_iso_beijing()
    with open(MATCHES) as f:
        matches = json.load(f)
    with open(MANIFEST_PARSED) as f:
        manifest_data = json.load(f)

    sop_conn = sqlite3.connect(str(SOP_DB))
    rail_conn = sqlite3.connect(str(RAIL_DB))

    batches = build_batches(now)
    print(f"准备插 {len(batches)} 个 release_batch:")
    for b in batches:
        print(f"  {b['id'][:12]}  {b['ship_name']:5s} {b['batch_sequence']} "
              f"{b['transport_mode']:10s} 车数={b['batch_count']:4d}  qty={b['batch_quantity']:.2f}t")
    n_b = insert_batches(sop_conn, batches)

    kunna_id = next(b["id"] for b in batches if b["ship_name"] == "昆娜" and b["batch_sequence"] == "lot01")
    kunna_bulk_id = next(b["id"] for b in batches if b["ship_name"] == "昆娜" and b["batch_sequence"] == "lot02")
    magritte_id = next(b["id"] for b in batches if b["ship_name"] == "玛格丽特" and b["batch_sequence"] == "lot01")
    magritte_bulk_id = next(b["id"] for b in batches if b["ship_name"] == "玛格丽特" and b["batch_sequence"] == "lot02")

    n_box = insert_container_shipments(
        sop_conn, rail_conn,
        matches["kunna_container"], matches["magritte_container"],
        kunna_id, magritte_id, manifest_data, now)
    n_bulk = insert_bulk_shipments(
        sop_conn, rail_conn,
        matches["kunna_bulk"], matches["magritte_bulk"],
        kunna_bulk_id, magritte_bulk_id, now)

    if apply:
        sop_conn.commit()
        print(f"\nCOMMIT: release_batches={n_b} container_boxes={n_box} bulk_wagons={n_bulk}")
    else:
        sop_conn.rollback()
        print(f"\nDRY-RUN: release_batches={n_b} container_boxes={n_box} bulk_wagons={n_bulk}")
        print("加 --apply 真正提交")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
