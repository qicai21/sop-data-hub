"""中唐特钢 2026 补录:旺达97 (大船:科恩)

plan=90260200023, qty=15800t, 合同 ZLDSZT-2026020603
供方:福建漳龙东山产业投资有限公司(上海新投)
海运段大船:科恩 / 转水到港:旺达97 (2/18-2/25)
车数 224,标载 15310t
"""
from __future__ import annotations
import sqlite3, json, hashlib, sys
from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"
RAIL_DB = REPO.parents[0] / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
JSON_DIR = Path("/Users/qicai21/Desktop/补项目数据/中唐/整理结果/by_ship_json")

BATCH = {
    "ship_name": "旺达97",
    "import_ship_name": "科恩",
    "plan_id": "90260200023",
    "contract_no": "ZLDSZT-2026020603",
    "batch_quantity": 15800.0,
    "cargo_name": "铁矿粉",
    "cargo_product_name": "Pb粉",
    "consignor": "沈阳盛京颐昇供应链管理有限公司",
    "consignee": "赤峰中唐特钢有限公司",
    "destination_station": "汐子",
    "origin_station": "高桥镇",
    "notice_date": "2026-02-17",     # 兜底 = 最早装车 2/18 前一天
    "batch_date": "2026-02-17",
    "batch_sequence": "lot01",
    "commissioner_note": (
        "海运段大船:科恩 / 转水到港:旺达97 / "
        "供方:福建漳龙东山产业投资有限公司(上海新投) / 港口:日照-锦州转水"
    ),
    "project": "zhongtang_special_steel",
}

SRC_SHIPS = ["旺达97"]
SOURCE_JSON_META = {
    "source": "backfill_2026-06-06",
    "plan_id": BATCH["plan_id"],
    "contract_no": BATCH["contract_no"],
    "import_ship_name": BATCH["import_ship_name"],
    "cargo_supplier": "福建漳龙东山产业投资有限公司(上海新投)",
    "transhipped_via": SRC_SHIPS,
    "port": "日照港-锦州港转水",
    "qty_t": BATCH["batch_quantity"],
}


def load_wagon_records():
    recs = []
    for ship in SRC_SHIPS:
        d = json.loads((JSON_DIR / f"{ship}.json").read_text(encoding="utf-8"))
        for w in d["wagons"]:
            rails = w.get("rail_records") or []
            if not rails:
                continue
            try:
                biz_dt = datetime.strptime(w["date"], "%Y-%m-%d")
            except (ValueError, TypeError, KeyError):
                biz_dt = None

            def gap(rr):
                if not biz_dt or not rr.get("ticketed_at"):
                    return 10**9
                try:
                    rt = datetime.fromisoformat(rr["ticketed_at"])
                except ValueError:
                    return 10**9
                return abs((rt - biz_dt).total_seconds())

            best = min(rails, key=gap)
            if not best.get("ydid"):
                continue
            recs.append({
                "car_no": w["car_no"],
                "ydid": best["ydid"],
                "ticket_no": w.get("ticket_no"),
                "biz_date": w.get("date"),
                "src_ship": ship,
            })
    return recs


def pull_95306_rows(ydids):
    rail = sqlite3.connect(str(RAIL_DB))
    rail.row_factory = sqlite3.Row
    out = {}
    for i in range(0, len(ydids), 200):
        chunk = ydids[i:i+200]
        ph = ",".join(["?"] * len(chunk))
        for r in rail.execute(f"SELECT * FROM shipments WHERE ydid IN ({ph})", chunk).fetchall():
            out[r["ydid"]] = dict(r)
    rail.close()
    return out


def main():
    biz_recs = load_wagon_records()
    print(f"商务 json 合并: {len(biz_recs)} 车")
    rail_map = pull_95306_rows([r["ydid"] for r in biz_recs])
    print(f"95306 命中: {len(rail_map)} / {len(biz_recs)}")
    if len(rail_map) != len(biz_recs):
        miss = [r for r in biz_recs if r["ydid"] not in rail_map]
        print("缺:", miss[:5])

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row

    batch_key = f"{BATCH['ship_name']}|{BATCH['cargo_name']}|{BATCH['destination_station']}|{BATCH['notice_date']}|{BATCH['batch_sequence']}"
    batch_id = hashlib.sha1(batch_key.encode()).hexdigest()
    actual_wagons = len(rail_map)

    if conn.execute("SELECT 1 FROM release_batches WHERE batch_key=?", (batch_key,)).fetchone():
        print(f"已存在,跳过 batch insert")
    else:
        print(f"创建 release_batch {batch_id[:12]}  key={batch_key}")
        conn.execute("""
          INSERT INTO release_batches (
            id, batch_key, project, contract_no, ship_name, import_ship_name,
            cargo_name, cargo_product_name, consignor, consignee,
            commissioner_note, destination_station, origin_station,
            notice_date, batch_date, batch_sequence, batch_quantity,
            total_planned_quantity, batch_count, actual_wagon_count,
            customer_name, plan_id, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, updated_at
          ) VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?, datetime('now'))
        """, (
            batch_id, batch_key, BATCH["project"], BATCH["contract_no"],
            BATCH["ship_name"], BATCH["import_ship_name"],
            BATCH["cargo_name"], BATCH["cargo_product_name"],
            BATCH["consignor"], BATCH["consignee"],
            BATCH["commissioner_note"], BATCH["destination_station"],
            BATCH["origin_station"],
            BATCH["notice_date"], BATCH["batch_date"], BATCH["batch_sequence"],
            BATCH["batch_quantity"], BATCH["batch_quantity"], 1, actual_wagons,
            BATCH["consignee"], BATCH["plan_id"], "completed",
            "汐子发运跟踪表+商务xlsx_backfill",
            json.dumps(SOURCE_JSON_META, ensure_ascii=False),
            f"{BATCH['ship_name']} {BATCH['import_ship_name']} {BATCH['cargo_product_name']} "
            f"{BATCH['plan_id']} {BATCH['contract_no']} 福建漳龙东山产业投资 上海新投",
            0,
        ))

    inserted = skipped = 0
    for biz in biz_recs:
        rail = rail_map.get(biz["ydid"])
        if not rail:
            continue
        wid = hashlib.sha1(f"{rail['ydid']}|{batch_id}".encode()).hexdigest()[:24]
        if conn.execute("SELECT 1 FROM wagon_shipments WHERE id=? OR (batch_id=? AND ydid=?)",
                        (wid, batch_id, rail["ydid"])).fetchone():
            skipped += 1
            continue
        conn.execute("""INSERT INTO wagon_shipments
            (id, batch_id, car_no, ydid, czydid, car_model, marked_weight,
             cargo_count, cargo_name, shipper_name, consignee_name,
             origin_name, destination_name, ticketed_at, departed_at,
             arrived_at, delivered_at, status_name, latest_stage_key,
             latest_stage_name, latest_event_time, accepted_at, loaded_at,
             transport_mode_code, transport_mode_name,
             container_no, container_numbers_json,
             project_id, ship_name, dispatch_status,
             source_message_id, source_group_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,datetime('now'),datetime('now'))""",
            (wid, batch_id, rail["car_no"], rail["ydid"], rail["czydid"],
             rail["car_model"],
             float(rail["marked_weight"]) if rail["marked_weight"] else None,
             int(rail["cargo_count"]) if rail["cargo_count"] else None,
             rail["cargo_name"], "", "",
             rail["origin_name"], rail["destination_name"], rail["ticketed_at"],
             rail["departed_at"], rail["arrived_at"], rail["delivered_at"],
             rail["status_name"], rail["latest_stage_key"], rail["latest_stage_name"],
             rail["latest_event_time"], rail["accepted_at"], rail["loaded_at"],
             rail["transport_mode_code"], rail["transport_mode_name"],
             rail["container_no_raw"] or "", rail["container_numbers_json"] or "",
             BATCH["project"], BATCH["ship_name"], "completed",
             f"backfill_wangda97|{biz['src_ship']}|{biz['ticket_no']}", ""))
        inserted += 1
    print(f"wagon_shipments: insert {inserted} 行 / skip {skipped} 行")
    conn.commit()
    conn.close()
    return batch_id


if __name__ == "__main__":
    bid = main()
    print(f"\n--- DONE,release_batch_id={bid[:12]} ---")
