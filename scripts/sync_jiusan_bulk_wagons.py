"""九三和谐1 散粮车(整车运输)95306 → sop 同步(可重复跑,幂等)。

集装箱走 sync_jiusan_harmony_wagons.py;散粮车(整车/L 型敞车)走本脚本。
散粮一节车 = 一张货票 = wagon_shipments 一行(无 box)。

每天散粮车发车后 95306 出新票,跑本脚本即可:
1. 拉 rail DB 高桥镇→新台子 大豆「整车运输」票(2026-06-09 起)
2. 每票一行 wagon_shipments(id=sha1("bulk"|ydid),与昆娜/玛格丽特散粮一致)
3. 新行 INSERT;已有行只刷 95306 状态(幂等,保留 created_at/source)
4. 重算 lot02 计数:actual_wagon_count / batch_count / shipped_weight_tons
   (装车重量按业务标载:L70=69t/车,其余 L16/L18=61t/车,非 95306 标重;
    jiusan 无 yaml shipped_weight_rule,故本脚本直接落重量,
    不走 shipped_weight.compute_for_release_batch。95306 marked_weight 仍原样留存)

用法:python scripts/sync_jiusan_bulk_wagons.py            # 干跑预览
      python scripts/sync_jiusan_bulk_wagons.py --apply    # 真正提交
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")

PROJECT = "jiusan"
SHIP = "和谐1"
BATCH_ID = "9d9d6e8364df7448119d2b28186c497b846a1cbb"  # jiusan|和谐1|lot02 散粮车
SINCE = "2026-06-09"
CONSIGNOR = "锦州港物流发展有限公司"
CONSIGNEE = "九三集团铁岭大豆科技有限公司"

# 装车重量(L70=69t/车、其余 L16/L18=61t/车)已收口到
# config/project_sops/jiusan.yaml 的 shipped_weight_rule.bulk,本脚本不再硬算,
# 落库 commit 后统一调 compute_for_release_batch(单一真相)。


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def map_dispatch_status(status_name: str) -> str:
    """95306 货票状态 → 本地 per-wagon dispatch_status。

    九三 lifecycle=full_track_to_received:货物已交付即视为已收货。
    其余(已发车/已到达)归 loading(发运中,在途/到站待卸)。
    """
    if status_name and "交付" in status_name:
        return "confirmed_received"
    return "loading"


def fetch_rail_tickets() -> list[dict]:
    conn = sqlite3.connect(str(RAIL_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ydid, czydid, car_no, car_model, cargo_name, shipper_name,
               consignee_name, origin_name, destination_name,
               accepted_at, loaded_at, ticketed_at, departed_at, arrived_at,
               delivered_at, status_name, latest_stage_key, latest_stage_name,
               latest_event_time, marked_weight, freight_fee,
               transport_mode_code, transport_mode_name
        FROM shipments
        WHERE cargo_name LIKE '%豆%'
          AND origin_name = '高桥镇'
          AND destination_name = '新台子'
          AND transport_mode_name LIKE '%整车%'
          AND ticketed_at >= ?
          AND car_no IS NOT NULL AND car_no != ''
        ORDER BY ticketed_at, car_no
        """,
        (SINCE,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_row(t: dict, now: str) -> dict:
    ds = map_dispatch_status(t["status_name"] or "")
    confirmed_at = t["delivered_at"] if ds == "confirmed_received" else None
    car_model = t["car_model"] or ""
    return {
        "id": stable_hash("bulk", t["ydid"]),
        "batch_id": BATCH_ID,
        "car_no": t["car_no"], "car_model": car_model,
        "cargo_name": t["cargo_name"] or "大豆",
        "shipper_name": t["shipper_name"] or CONSIGNOR,
        "consignee_name": t["consignee_name"] or CONSIGNEE,
        "origin_name": t["origin_name"], "destination_name": t["destination_name"],
        "accepted_at": t["accepted_at"] or "", "loaded_at": t["loaded_at"] or "",
        "ticketed_at": t["ticketed_at"] or "", "departed_at": t["departed_at"] or "",
        "arrived_at": t["arrived_at"] or "", "delivered_at": t["delivered_at"] or "",
        "status_name": t["status_name"] or "",
        "latest_stage_key": t["latest_stage_key"] or "",
        "latest_stage_name": t["latest_stage_name"] or "",
        "latest_event_time": t["latest_event_time"] or "",
        "marked_weight": _f(t["marked_weight"]),
        "freight_fee": t["freight_fee"],
        "ydid": t["ydid"], "czydid": t["czydid"],
        "transport_mode_code": t["transport_mode_code"],
        "transport_mode_name": t["transport_mode_name"],
        "project_id": PROJECT, "ship_name": SHIP,
        "dispatch_status": ds,
        "source_message_id": f"auto_harmony1_bulk_sync_{t['ticketed_at'][:10]}",
        "source_group_id": "",
        "confirmed_received_at": confirmed_at,
        "created_at": now, "updated_at": now,
    }


def upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    existing = {r[0] for r in conn.execute(
        "SELECT id FROM wagon_shipments WHERE batch_id=?", (BATCH_ID,))}
    new_n = 0
    for r in rows:
        if r["id"] in existing:
            # 已有行只刷 95306 状态字段(在途→到站→交付的推进),保留 created_at。
            # computed_loading_weight/weight_rule_basis 由 compute_for_release_batch 填。
            conn.execute(
                """UPDATE wagon_shipments SET
                   status_name=?, latest_stage_key=?, latest_stage_name=?,
                   latest_event_time=?, departed_at=?, arrived_at=?, delivered_at=?,
                   dispatch_status=?, confirmed_received_at=?, marked_weight=?,
                   updated_at=? WHERE id=?""",
                (r["status_name"], r["latest_stage_key"], r["latest_stage_name"],
                 r["latest_event_time"], r["departed_at"], r["arrived_at"],
                 r["delivered_at"], r["dispatch_status"], r["confirmed_received_at"],
                 r["marked_weight"], r["updated_at"], r["id"]),
            )
        else:
            cols = list(r.keys())
            conn.execute(
                f"INSERT INTO wagon_shipments ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [r[c] for c in cols],
            )
            new_n += 1
    return new_n, len(rows) - new_n


def update_counts(conn: sqlite3.Connection, now: str) -> dict:
    """更新 batch_count(车票数);装车重量 / actual_wagon_count 走统一 yaml 规则,
    由 main() commit 后调 compute_for_release_batch 落。"""
    # cars = 已发"车次"= 货票数,按 ydid 去重(**不是 car_no**)。散粮 K 车循环
    # 复用,按车号去重会吞掉复用车次(6/14 那 50 台 6/16 再装一趟 = 多 50 车次)。
    agg = conn.execute(
        """SELECT COUNT(*) n, COUNT(DISTINCT ydid) cars,
                  SUM(CASE WHEN dispatch_status='confirmed_received' THEN 1 ELSE 0 END) delivered
           FROM wagon_shipments WHERE batch_id=?""",
        (BATCH_ID,),
    ).fetchone()
    n, cars, delivered = agg[0], agg[1], agg[2]
    conn.execute(
        "UPDATE release_batches SET batch_count=?, updated_at=? WHERE id=?",
        (cars, now, BATCH_ID),
    )
    return {"wagons": n, "cars": cars, "delivered": delivered}


def main(apply: bool = False) -> None:
    now = now_iso_beijing()
    tickets = fetch_rail_tickets()
    if not tickets:
        print("rail DB 无散粮票")
        return
    from collections import defaultdict
    byday = defaultdict(lambda: [0, 0.0])
    for t in tickets:
        d = (t["ticketed_at"] or "")[:10]
        byday[d][0] += 1
        byday[d][1] += _f(t["marked_weight"])
    print(f"95306 散粮票 {len(tickets)} 张:")
    for d in sorted(byday):
        print(f"  制票日 {d}: {byday[d][0]} 车 / 标重 {byday[d][1]:.1f}t")

    conn = sqlite3.connect(str(SOP_DB))
    try:
        rows = [build_row(t, now) for t in tickets]
        new_n, ref_n = upsert_rows(conn, rows)
        counts = update_counts(conn, now)
        if apply:
            conn.commit()
            # 装车重量统一走 yaml shipped_weight_rule.bulk(独立连接,需先 commit)
            from sop_hub.sop.shipped_weight import compute_for_release_batch
            sw = compute_for_release_batch(BATCH_ID, db_path=SOP_DB)
            print(f"\nCOMMIT ✓ 新增 {new_n} 车 / 刷新 {ref_n} 车")
            print(f"  lot02: {counts['cars']} 车,已发运 {sw.get('shipped_weight_tons')}t "
                  f"(已交付 {counts['delivered']} 车,yaml L70=69/其余=61),"
                  f"剩余 {sw.get('remaining_weight_tons')}t")
        else:
            conn.rollback()
            print(f"\nDRY-RUN(加 --apply 提交)新增 {new_n} 车 / 刷新 {ref_n} 车")
            print(f"  lot02: {counts['cars']} 车(已交付 {counts['delivered']});"
                  f"装车重量在 --apply 时按 yaml 规则计算")
    finally:
        conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
