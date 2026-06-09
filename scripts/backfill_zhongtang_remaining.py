"""中唐特钢 2026 补录:剩余 8 船(plan/合同/品名 后补)

非凡 / 德邻惠航 / 埃克尔_公正混 / 喜悦 / 公正 / 金泰68 / 厦门世纪 / 环球信任

每船入一个 release_batch lot01,plan_id/contract_no/cargo_product_name 暂空,
等用户补具体合同后再 enrich(就地 UPDATE,无需重建)。

数据来源:商务 xlsx 整理出来的 by_ship_json + 95306 ydid(选 ticketed_at 离
商务 date 最近的那条,避开同辆车皮二次发运的 ydid)。

车皮维度:dispatch_status='completed' (商务表上的全部已发完到汐子)。
release_batch 维度:dispatch_status='completed'(同上)。
batch_quantity:暂用商务表实重总数(等 plan 来了校正)。
notice_date:兜底 = 最早装车日前推 1 天。
"""
from __future__ import annotations
import sqlite3, json, hashlib, sys
from pathlib import Path
from datetime import datetime, timedelta

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"
RAIL_DB = REPO.parents[0] / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"
JSON_DIR = Path("/Users/qicai21/Desktop/补项目数据/中唐/整理结果/by_ship_json")

# ────────────── 8 船共用 / 个别覆盖 ──────────────
DEFAULTS = {
    "cargo_name": "铁矿粉",
    "consignee": "赤峰中唐特钢有限公司",
    "destination_station": "汐子",
    "batch_sequence": "lot01",
    "project": "zhongtang_special_steel",
    "dispatch_status": "completed",
}
AGENT_TO_CONSIGNOR = {
    "沈阳颐昇": "沈阳盛京颐昇供应链管理有限公司",
    "新铁晟":   "锦州新僡物流有限公司",          # OCR 归一闭集
}
ORIGIN_MAP = {  # by_ship_json 的 origin 有"(沈)"/罗马括号去掉
    "高桥镇（沈）": "高桥镇",
    "霍林河(沈）":  "霍林河",
}

SHIPS = [
    "非凡", "德邻惠航", "埃克尔_公正混", "喜悦",
    "公正", "金泰68", "厦门世纪", "环球信任",
]


def pick_best_rail(rails, biz_date):
    """rail_records 默认按 ticketed_at DESC;挑跟商务 date 最近的避开二次发运。"""
    try:
        biz_dt = datetime.strptime(biz_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        biz_dt = None

    def gap(rr):
        if not biz_dt or not rr.get("ticketed_at"):
            return 10**9
        try:
            return abs((datetime.fromisoformat(rr["ticketed_at"]) - biz_dt).total_seconds())
        except ValueError:
            return 10**9
    return min(rails, key=gap)


def collect(ship):
    d = json.loads((JSON_DIR / f"{ship}.json").read_text(encoding="utf-8"))
    recs = []
    for w in d["wagons"]:
        rails = w.get("rail_records") or []
        if not rails:
            continue
        best = pick_best_rail(rails, w.get("date"))
        if not best.get("ydid"):
            continue
        recs.append({
            "car_no": w["car_no"],
            "ydid": best["ydid"],
            "ticket_no": w.get("ticket_no"),
            "biz_date": w.get("date"),
            "actual_weight": w.get("actual_weight"),
        })
    biz_dates = sorted({r["biz_date"] for r in recs if r.get("biz_date")})
    earliest = datetime.strptime(biz_dates[0], "%Y-%m-%d") if biz_dates else None
    return d, recs, earliest


def pull_95306(conn_rail, ydids):
    out = {}
    for i in range(0, len(ydids), 200):
        chunk = ydids[i:i+200]
        ph = ",".join(["?"] * len(chunk))
        for r in conn_rail.execute(f"SELECT * FROM shipments WHERE ydid IN ({ph})", chunk).fetchall():
            out[r["ydid"]] = dict(r)
    return out


def backfill_ship(ship, conn, conn_rail):
    meta, biz_recs, earliest_dt = collect(ship)
    rail_map = pull_95306(conn_rail, [r["ydid"] for r in biz_recs])

    consignor = AGENT_TO_CONSIGNOR.get(meta["agent"]) or meta["shipper"]
    origin = ORIGIN_MAP.get(meta["origin"]) or meta["origin"]
    actual_total_tons = sum(r["actual_weight"] or 0 for r in biz_recs)

    notice_date = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d") if earliest_dt else "2026-01-01"

    batch_key = f"{ship}|{DEFAULTS['cargo_name']}|{DEFAULTS['destination_station']}|{notice_date}|{DEFAULTS['batch_sequence']}"
    batch_id = hashlib.sha1(batch_key.encode("utf-8")).hexdigest()

    src_files = meta.get("source_files") or []
    src_file_name = "汐子发运跟踪表+商务xlsx_backfill | " + " | ".join(src_files)

    source_json = {
        "source": "backfill_2026-06-06",
        "ship": ship,
        "agent": meta["agent"],
        "origin": meta["origin"],
        "dest": meta["dest"],
        "shipper": meta["shipper"],
        "receiver": meta["receiver"],
        "source_files": src_files,
        "note": "plan_id / contract_no / cargo_product_name / import_ship_name 待用户后补",
        "biz_actual_tons": actual_total_tons,
        "biz_wagons": len(biz_recs),
    }

    commissioner_note = f"待补充:大船+plan+合同 [from 商务 backfill,代理 {meta['agent']}]"
    searchable_text = f"{ship} {DEFAULTS['cargo_name']} {DEFAULTS['destination_station']} {consignor} 待补合同"

    actual_wagons = len(rail_map)

    if conn.execute("SELECT 1 FROM release_batches WHERE batch_key=?", (batch_key,)).fetchone():
        print(f"  [{ship}] 已存在 batch_key,跳过 batch insert")
    else:
        print(f"  [{ship}] 新建 batch {batch_id[:12]}  notice={notice_date}  车{actual_wagons}/实重{actual_total_tons}t")
        conn.execute("""
          INSERT INTO release_batches (
            id, batch_key, project, ship_name,
            cargo_name, consignor, consignee,
            commissioner_note, destination_station, origin_station,
            notice_date, batch_date, batch_sequence,
            batch_quantity, total_planned_quantity, batch_count, actual_wagon_count,
            customer_name, dispatch_status,
            source_file_name, source_json, searchable_text,
            is_weighed, updated_at
          ) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?, ?,?, ?,?,?, ?, datetime('now'))
        """, (
            batch_id, batch_key, DEFAULTS["project"], ship,
            DEFAULTS["cargo_name"], consignor, DEFAULTS["consignee"],
            commissioner_note, DEFAULTS["destination_station"], origin,
            notice_date, notice_date, DEFAULTS["batch_sequence"],
            actual_total_tons, actual_total_tons, 1, actual_wagons,
            DEFAULTS["consignee"], DEFAULTS["dispatch_status"],
            src_file_name, json.dumps(source_json, ensure_ascii=False), searchable_text,
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
             DEFAULTS["project"], ship, "completed",
             f"backfill_remaining|{ship}|{biz['ticket_no']}", ""))
        inserted += 1
    print(f"             wagons insert={inserted} / skip={skipped}")
    return batch_id


def main():
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    conn_rail = sqlite3.connect(str(RAIL_DB))
    conn_rail.row_factory = sqlite3.Row

    results = []
    for ship in SHIPS:
        bid = backfill_ship(ship, conn, conn_rail)
        results.append((ship, bid))
    conn.commit()
    conn.close()
    conn_rail.close()

    print(f"\n=== 8 船补录完成 ===")
    for ship, bid in results:
        print(f"  {ship:14s} -> {bid[:12]}")


if __name__ == "__main__":
    main()
