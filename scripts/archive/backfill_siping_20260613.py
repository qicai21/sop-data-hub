"""一次性回填:四平 2026-06-13 发车(煤一,wx_876)。

链 09:22 跑时本地 95306 还没同步到这 37 票(server 09:19-09:21 制单),
create_wagon_shipments 退 no_candidates;且 jilin 链一次只处理一条船,这条
消息有两条船。本脚本按手写检车清单(四平1/四平2.jpg)的船归属 + 箱号 join
95306,把 37 车 box 级回填到两个 release_batch。

船归属(序号→船,排车 5445976/4402976 已剔除,已对 95306 校验 18+19=37):
  马兰希望 lot01 = 序号 1-18(18 车)
  蓝鳍     lot07 = 序号 19-39 装车(19 车)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")

BATCHES = {
    "马兰希望": {
        "batch_id": "8e412228279a8fe18e5054c81f2896ca08d5bd0c",
        "cars": ['5738441','5493062','5323173','5798976','5729252','5232192',
                 '5789690','5791516','1839060','1617466','1731882','1797862',
                 '1705234','1719696','1851475','1612377','1513970','1754362'],
    },
    "蓝鳍": {
        "batch_id": "2a954eb838a6b1f2a8b1a17952140d07400094b6",
        "cars": ['1710616','1580517','1729598','1608567','1506440','1741681',
                 '1636208','1656892','1807649','1717484','1758778','1574363',
                 '1802186','5313868','5791769','1679248','1833400','1804394','1809893'],
    },
}


SMID = "backfill_siping_wx876_2026-06-13"


def h24(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def h16(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def main() -> None:
    now = now_iso_beijing()
    rail = sqlite3.connect(f"file:{RAIL_DB}?mode=ro", uri=True)
    rail.row_factory = sqlite3.Row
    conn = sqlite3.connect(str(SOP_DB))

    # 清掉前一版仅 box(40 位 id)的回填,重做干净双写
    conn.execute("DELETE FROM wagon_container_shipments WHERE source_message_id=?", (SMID,))
    conn.commit()

    total_box = 0
    for ship, info in BATCHES.items():
        bid = info["batch_id"]
        departure_id = h16(bid, ship)
        ship_box = ship_car = 0
        for car in info["cars"]:
            r = rail.execute(
                "SELECT ydid, czydid, car_no, car_model, container_numbers_json, "
                "marked_weight, status_name, latest_stage_key, latest_stage_name, "
                "latest_event_time, accepted_at, loaded_at, ticketed_at, departed_at, "
                "arrived_at, delivered_at, transport_mode_code, transport_mode_name, "
                "origin_name, destination_name, cargo_name "
                "FROM shipments WHERE car_no=? AND destination_name LIKE '%四平%' "
                "AND ticketed_at>='2026-06-13'", (car,),
            ).fetchone()
            if not r:
                print(f"  ⚠ {ship} 车 {car} 在 95306 没找到,跳过")
                continue
            boxes = json.loads(r["container_numbers_json"] or "[]")
            container_no = "/".join(boxes)
            # ── 车级 wagon_shipments(excel 事件锚)──
            conn.execute(
                """INSERT OR REPLACE INTO wagon_shipments (
                    id, departure_id, batch_id, car_no, car_model,
                    cargo_name, origin_name, destination_name,
                    ticketed_at, departed_at, arrived_at, delivered_at, confirmed_received_at,
                    container_no, waybill_no, ydid,
                    project_id, ship_name, dispatch_status,
                    source_message_id, source_group_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (h24(r["ydid"], bid), departure_id, bid, r["car_no"], r["car_model"] or "",
                 r["cargo_name"], r["origin_name"], r["destination_name"],
                 r["ticketed_at"], r["departed_at"] or "", r["arrived_at"] or "",
                 r["delivered_at"] or "", "",
                 container_no, "", r["ydid"],
                 "jilin_jingang_jinzhou", ship, "pending",
                 SMID, "铁晟业务工作群"),
            )
            ship_car += 1
            # ── box 级 wagon_container_shipments ──
            for pos, box in enumerate(boxes, start=1):
                row = {
                    "id": h24(r["car_no"], box, r["ydid"]),
                    "car_no": r["car_no"], "box_no": box, "box_position": pos,
                    "ydid": r["ydid"], "czydid": r["czydid"], "waybill_no": "",
                    "batch_id": bid, "car_model": r["car_model"] or "",
                    "ticketed_at": r["ticketed_at"], "departed_at": r["departed_at"] or "",
                    "arrived_at": r["arrived_at"] or "", "delivered_at": r["delivered_at"] or "",
                    "accepted_at": r["accepted_at"] or "", "loaded_at": r["loaded_at"] or "",
                    "status_name": r["status_name"] or "",
                    "latest_stage_key": r["latest_stage_key"] or "",
                    "latest_stage_name": r["latest_stage_name"] or "",
                    "latest_event_time": r["latest_event_time"] or "",
                    "origin_name": r["origin_name"], "destination_name": r["destination_name"],
                    "transport_mode_code": r["transport_mode_code"],
                    "transport_mode_name": r["transport_mode_name"],
                    "cargo_name": r["cargo_name"], "marked_weight": r["marked_weight"],
                    "project_id": "jilin_jingang_jinzhou", "ship_name": ship,
                    "dispatch_status": "in_progress",
                    "source_message_id": SMID,
                    "source_group_id": "铁晟业务工作群",
                    "created_at": now, "updated_at": now,
                }
                cols = list(row.keys())
                conn.execute(
                    f"INSERT OR REPLACE INTO wagon_container_shipments ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols],
                )
                ship_box += 1
        # 重算 batch 计数 + 推进 loading
        n_ydid, n_box = conn.execute(
            "SELECT count(DISTINCT ydid), count(*) FROM wagon_container_shipments WHERE batch_id=?",
            (bid,),
        ).fetchone()
        conn.execute(
            "UPDATE release_batches SET batch_count=?, actual_wagon_count=?, "
            "dispatch_status='loading', dispatch_status_note='backfill wx876 2026-06-13', "
            "dispatch_status_updated_at=?, updated_at=? WHERE id=?",
            (n_ydid, n_ydid, now, now, bid),
        )
        print(f"  {ship} lot: {ship_car} 车级行 + {ship_box} box 行(累计 {n_ydid} 车/{n_box} box)")
        total_box += ship_box

    conn.commit()
    conn.close()
    rail.close()
    print(f"COMMIT ✓ 共回填 {total_box} box")


if __name__ == "__main__":
    main()
