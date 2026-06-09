"""吉林金钢 蓝鳍 lot06 — 50 节装车 ingest (2026-06-06 21:28 检车清单)

铁晟群 6/6 21:28 A静候佳音 发了 3 张检车清单图(seq 364-366)+ 1 条文本
(seq 367 "煤六 50节 四平铁 蓝鳍"),但 daemon 自锁导致 live_service upsert
fail,链没触发。jilin 没有 inspection chain executor(generic_sop_task),所以
即使 inbox 写成功也不会自动入。

95306 已同步:50 节 2026-06-06 21:24-21:27 高桥镇→四平 (origin 锦州/锦港新德
转铁运,destination_name 是终点站四平),跟检车清单完全对得上。

本脚本:把 50 个 ydid 写入 wagon_shipments 挂到 lot06,触发 shipped_weight rerun。
"""
from __future__ import annotations
import sqlite3, json, hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "data" / "sop_agent.db"
RAIL_DB = REPO.parents[0] / "rail95306-sync" / "runtime" / "95306_collection.sqlite3"

BATCH_ID = "5d5bb25996c7d2003545b7174851b6cbddd3f25b"  # 蓝鳍 lot06
PROJECT = "jilin_jingang_jinzhou"
SHIP = "蓝鳍"


def main():
    rail = sqlite3.connect(str(RAIL_DB))
    rail.row_factory = sqlite3.Row
    rows = rail.execute(
        "SELECT * FROM shipments WHERE ticketed_at >= '2026-06-06 19:00' "
        "AND ticketed_at <= '2026-06-07 00:00' AND destination_name='四平' "
        "AND origin_name='高桥镇' ORDER BY ticketed_at"
    ).fetchall()
    print(f"95306 50 节: {len(rows)}")
    rail.close()

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row

    inserted = skipped = 0
    for rail_r in rows:
        rail = dict(rail_r)
        wid = hashlib.sha1(f"{rail['ydid']}|{BATCH_ID}".encode()).hexdigest()[:24]
        if conn.execute(
            "SELECT 1 FROM wagon_shipments WHERE id=? OR (batch_id=? AND ydid=?)",
            (wid, BATCH_ID, rail["ydid"]),
        ).fetchone():
            skipped += 1
            continue
        conn.execute(
            """INSERT INTO wagon_shipments
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
            (
                wid, BATCH_ID, rail["car_no"], rail["ydid"], rail["czydid"],
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
                PROJECT, SHIP, "in_progress",
                "lanqi_lot06_50|wx_seq367|铁晟21:28", "",
            ),
        )
        inserted += 1

    # 更新 release_batch 计数 + shipped_weight
    total = conn.execute(
        "SELECT count(*), COALESCE(SUM(marked_weight),0) FROM wagon_shipments WHERE batch_id=?",
        (BATCH_ID,),
    ).fetchone()
    cnt, sw = total[0], total[1]
    conn.execute(
        "UPDATE release_batches SET actual_wagon_count=?, batch_quantity=COALESCE(?, batch_quantity), updated_at=datetime('now') WHERE id=?",
        (cnt, sw if sw else None, BATCH_ID),
    )
    conn.commit()
    conn.close()
    print(f"inserted={inserted} skipped={skipped}")
    print(f"lot06 total wagons={cnt} shipped_weight={sw}t")


if __name__ == "__main__":
    main()
