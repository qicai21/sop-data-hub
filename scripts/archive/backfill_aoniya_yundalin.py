"""中唐特钢 2026 补录:爱奥尼亚(运达7 + 德邻慧海 2 段转水)

plan=90260200038, qty=35000t, 合同 ZLDSZT-2026021011
供方:福建漳龙东山产业投资有限公司(瑞钢联)
海运段大船:爱奥尼亚 / 转水到港:运达7(2/24-2/27) + 德邻慧海(2/27-3/8)
车数预计 510(运达7 242 + 德邻惠海 268),总实重 34330t
"""
from __future__ import annotations
import sqlite3, json, hashlib, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"
RAIL_DB = REPO.parents[0] / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
JSON_DIR = Path("/Users/qicai21/Desktop/补项目数据/中唐/整理结果/by_ship_json")

# ────────────── release_batch 元信息 ──────────────
BATCH = {
    "ship_name": "运达7/德邻慧海",       # 用户指定 — 2 条转水船合并
    "import_ship_name": "爱奥尼亚",      # 海运大船段
    "plan_id": "90260200038",
    "contract_no": "ZLDSZT-2026021011",
    "batch_quantity": 35000.0,
    "cargo_name": "铁矿粉",
    "cargo_product_name": "Pb粉",
    "consignor": "沈阳盛京颐昇供应链管理有限公司",
    "consignee": "赤峰中唐特钢有限公司",
    "destination_station": "汐子",
    "origin_station": "高桥镇",
    "notice_date": "2026-02-22",   # 没放货单兜底:最早装车日(2/23 制票)往前一天
    "batch_date": "2026-02-22",
    "batch_sequence": "lot01",
    "commissioner_note": (
        "海运段大船:爱奥尼亚 / 转水到港:运达7+德邻慧海 / "
        "供方:福建漳龙东山产业投资有限公司(瑞钢联) / 港口:日照-锦州转水"
    ),
    "project": "zhongtang_special_steel",
}

SOURCE_JSON_META = {
    "source": "backfill_2026-06-06",
    "plan_id": BATCH["plan_id"],
    "contract_no": BATCH["contract_no"],
    "import_ship_name": BATCH["import_ship_name"],
    "cargo_supplier": "福建漳龙东山产业投资有限公司(瑞钢联)",
    "transhipped_via": ["运达7", "德邻慧海"],
    "port": "日照港-锦州港转水",
    "qty_t": BATCH["batch_quantity"],
    "merged_from_ships_xlsx": ["运达7", "德邻惠海"],
}


def load_wagon_records():
    """读 运达7 + 德邻惠海 商务 json,挑 ticketed_at 离商务 date 最近的 rail_record。

    rail_records 默认按 ticketed_at DESC,直接取 [0] 会取到这辆车**后续二次发运**
    的 ydid(同辆车皮可能多次往汐子拉货),把不属于这票的 ydid 当成这票的。
    正确做法:对每辆车,选 rail_records 里 ticketed_at 跟商务 date 差值最小的那条。
    """
    from datetime import datetime
    recs = []
    for ship in ["运达7", "德邻惠海"]:
        d = json.loads((JSON_DIR / f"{ship}.json").read_text(encoding="utf-8"))
        for w in d["wagons"]:
            rails = w.get("rail_records") or []
            if not rails:
                print(f"  WARN: {ship} car_no={w['car_no']} 没有 rail_records")
                continue
            biz_date = w.get("date")  # e.g. "2026-2-24"
            try:
                biz_dt = datetime.strptime(biz_date, "%Y-%m-%d")
            except (ValueError, TypeError):
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
                "biz_marked_weight": w.get("marked_weight"),
                "biz_actual_weight": w.get("actual_weight"),
                "biz_date": w.get("date"),
                "src_ship": ship,
            })
    return recs


def pull_95306_rows(ydids):
    rail = sqlite3.connect(str(RAIL_DB))
    rail.row_factory = sqlite3.Row
    out = {}
    BATCH_SIZE = 200
    for i in range(0, len(ydids), BATCH_SIZE):
        chunk = ydids[i:i + BATCH_SIZE]
        ph = ",".join(["?"] * len(chunk))
        rows = rail.execute(f"SELECT * FROM shipments WHERE ydid IN ({ph})", chunk).fetchall()
        for r in rows:
            out[r["ydid"]] = dict(r)
    rail.close()
    return out


def main(apply: bool = True):
    biz_recs = load_wagon_records()
    print(f"商务 json 合并: {len(biz_recs)} 车")
    ydids = [r["ydid"] for r in biz_recs]
    rail_map = pull_95306_rows(ydids)
    print(f"95306 命中: {len(rail_map)} / {len(ydids)}")
    miss = [r for r in biz_recs if r["ydid"] not in rail_map]
    if miss:
        print(f"WARN: {len(miss)} 车 95306 没找到,示例:")
        for r in miss[:5]:
            print(f"  {r['src_ship']} | {r['car_no']} | {r['ydid']}")

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row

    # 1. release_batches 入库 ──────────────────────────────────────────────
    batch_key = f"{BATCH['ship_name']}|{BATCH['cargo_name']}|{BATCH['destination_station']}|{BATCH['notice_date']}|{BATCH['batch_sequence']}"
    batch_id = hashlib.sha1(batch_key.encode("utf-8")).hexdigest()
    actual_wagons = len(rail_map)

    existing = conn.execute("SELECT id FROM release_batches WHERE batch_key=? OR id=?",
                             (batch_key, batch_id)).fetchone()
    if existing:
        print(f"已存在 release_batch {existing['id']},跳过 insert")
    else:
        print(f"创建 release_batch {batch_id[:12]}  key={batch_key}")
        if apply:
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
                f"{BATCH['plan_id']} {BATCH['contract_no']} 福建漳龙东山产业投资 瑞钢联",
                0,
            ))

    # 2. wagon_shipments 入库 ─────────────────────────────────────────────
    inserted = skipped = 0
    for biz in biz_recs:
        rail = rail_map.get(biz["ydid"])
        if not rail:
            continue
        wid = hashlib.sha1(f"{rail['ydid']}|{batch_id}".encode()).hexdigest()[:24]
        existing = conn.execute("SELECT 1 FROM wagon_shipments WHERE id=? OR (batch_id=? AND ydid=?)",
                                 (wid, batch_id, rail["ydid"])).fetchone()
        if existing:
            skipped += 1
            continue
        if apply:
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
                 f"backfill_aoniya|{biz['src_ship']}|{biz['ticket_no']}", ""))
            inserted += 1
    print(f"wagon_shipments: insert {inserted} 行 / skip {skipped} 行(已存在)")

    if apply:
        conn.commit()
    conn.close()
    return batch_id


if __name__ == "__main__":
    apply = "--apply" in sys.argv or True
    bid = main(apply=apply)
    print(f"\n--- DONE,release_batch_id={bid[:12]} ---")
