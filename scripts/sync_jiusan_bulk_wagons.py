"""九三散粮车(整车运输)95306 → sop 同步,**多船拆分版**(可重复跑,幂等)。

#issue-20260620-散粮sync按船拆分:原版写死 SHIP="和谐1",多船并行期(诚信+和谐1
同列发出)把诚信的散粮全算进和谐1 → 和谐1 lot02 超发(剩余 -2072t)、诚信 lot02=0。
95306 货票里诚信/和谐1 到站(新台子)、收货人全相同,**无字段能区分船**;唯一事实源
= 港方每趟「简装车通知单」(落在 `bulk_loading_notice_wagon` 表,标了船名+逐车ydid)。

本版改为**按 ydid 查通知单台账分船路由**:
1. 拉 rail DB 高桥镇→新台子 大豆「整车运输」票(2026-06-09 起)
2. 每票 id=sha1("bulk"|ydid)(船无关,故重路由不换 id);按 ydid 查台账定 ship→lot02 batch
3. **命中台账**:路由到该船 lot02;已存在但挂错船的 → **重路由纠正**(自愈)
4. **未命中**:已存在行原样保留(grandfather,历史单船期和谐1不动);全新行暂落和谐1 + WARN
   —— 通知单一入台账,下趟 sync 自动把它纠正,污染临时且自消
5. 按**每条被改动的 batch** 分别重算 batch_count + 装车重量(yaml shipped_weight_rule.bulk)

集装箱走 sync_jiusan_harmony_wagons.py;散粮车(整车/L 型敞车)走本脚本。

用法:python scripts/sync_jiusan_bulk_wagons.py            # 干跑预览(分船)
      python scripts/sync_jiusan_bulk_wagons.py --apply    # 真正提交
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")

PROJECT = "jiusan"
SINCE = "2026-06-09"
CONSIGNOR = "锦州港物流发展有限公司"
CONSIGNEE = "九三集团铁岭大豆科技有限公司"
# 未命中台账的全新散粮车的兜底落点(单船期/通知单未到时)。命中台账的会自愈纠正,
# 故此兜底只是临时落点;不要因此放弃 WARN。和谐1 发完后不再默认堆它:兜底 = 当前唯一
# 活跃船(lot02 loading);无/多个活跃船时退回 FALLBACK_SHIP_DEFAULT(#issue-20260627)。
FALLBACK_SHIP_DEFAULT = "和谐1"


def resolve_fallback_ship(conn: sqlite3.Connection) -> str:
    """未命中台账的全新散粮车落点 = 当前唯一在发(loading)的 lot02 船。
    恰好一个活跃船 → 它;否则 → FALLBACK_SHIP_DEFAULT。"""
    loading = [
        r["ship_name"] for r in conn.execute(
            "SELECT ship_name FROM release_batches WHERE project=? AND batch_sequence='lot02' "
            "AND dispatch_status='loading' AND ship_name IS NOT NULL", (PROJECT,))
    ]
    return loading[0] if len(loading) == 1 else FALLBACK_SHIP_DEFAULT


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


def load_ship_batches(conn: sqlite3.Connection) -> dict[str, str]:
    """九三各船 lot02 散粮 batch:ship_name → release_batches.id(单一事实源,不硬编码)。"""
    return {
        r["ship_name"]: r["id"]
        for r in conn.execute(
            "SELECT id, ship_name FROM release_batches "
            "WHERE project=? AND batch_sequence='lot02' AND ship_name IS NOT NULL",
            (PROJECT,),
        )
    }


def load_notice_ship_map(conn: sqlite3.Connection) -> dict[str, str]:
    """简装车通知单台账:ydid → ship_name(拆船唯一事实源)。"""
    return {
        r["ydid"]: r["ship_name"]
        for r in conn.execute(
            "SELECT ydid, ship_name FROM bulk_loading_notice_wagon "
            "WHERE ydid IS NOT NULL AND ydid != ''"
        )
    }


def build_row(t: dict, now: str, ship: str, batch_id: str) -> dict:
    ds = map_dispatch_status(t["status_name"] or "")
    confirmed_at = t["delivered_at"] if ds == "confirmed_received" else None
    car_model = t["car_model"] or ""
    return {
        "id": stable_hash("bulk", t["ydid"]),
        "batch_id": batch_id,
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
        "project_id": PROJECT, "ship_name": ship,
        "dispatch_status": ds,
        "source_message_id": f"auto_jiusan_bulk_sync_{ship}_{(t['ticketed_at'] or '')[:10]}",
        "source_group_id": "",
        "confirmed_received_at": confirmed_at,
        "created_at": now, "updated_at": now,
    }


def resolve_routing(
    tickets: list[dict], notice_map: dict[str, str], ship_batches: dict[str, str],
    fallback_ship: str,
) -> list[tuple[dict, str, str, bool]]:
    """每票 → (ticket, ship, batch_id, matched)。matched=命中简装车通知单台账(按ydid)。"""
    routed = []
    for t in tickets:
        ship = notice_map.get(t["ydid"])
        if ship and ship in ship_batches:
            routed.append((t, ship, ship_batches[ship], True))
        else:
            routed.append((t, fallback_ship, ship_batches.get(fallback_ship, ""), False))
    return routed


# 刷新已有行的 95306 状态字段(在途→到站→交付推进),不动 batch/ship。
_STATUS_SET = (
    "status_name=?, latest_stage_key=?, latest_stage_name=?, latest_event_time=?, "
    "departed_at=?, arrived_at=?, delivered_at=?, dispatch_status=?, "
    "confirmed_received_at=?, marked_weight=?, updated_at=?"
)


def _status_vals(r: dict) -> list:
    return [
        r["status_name"], r["latest_stage_key"], r["latest_stage_name"],
        r["latest_event_time"], r["departed_at"], r["arrived_at"], r["delivered_at"],
        r["dispatch_status"], r["confirmed_received_at"], r["marked_weight"], r["updated_at"],
    ]


def upsert_rows(
    conn: sqlite3.Connection, routed: list[tuple[dict, str, str, bool]],
    now: str, ship_batches: dict[str, str],
) -> dict:
    # 现有九三散粮行当前所在 batch(跨全部九三 lot02,以便发现"诚信票错挂和谐1")。
    jb = [b for b in ship_batches.values() if b]
    existing: dict[str, str] = {}
    if jb:
        qm = ",".join("?" * len(jb))
        for r in conn.execute(
            f"SELECT id, batch_id FROM wagon_shipments WHERE batch_id IN ({qm})", jb
        ):
            existing[r["id"]] = r["batch_id"]

    new_n = reroute_n = refresh_n = 0
    touched: set[str] = set()
    warn_new_unmatched: list[tuple] = []
    # 归属变了 → 清核对完毕标记(否则 reconcile 把它当已核对剔除,脏标记卡死)
    rec_clear = (", reconciled_at=NULL, reconcile_source_ref=NULL"
                 if "reconciled_at" in {c[1] for c in conn.execute("PRAGMA table_info(wagon_shipments)")}
                 else "")

    for t, ship, batch_id, matched in routed:
        rid = stable_hash("bulk", t["ydid"])
        r = build_row(t, now, ship, batch_id)
        if rid in existing:
            cur = existing[rid]
            if matched and batch_id and batch_id != cur:
                # 命中台账但当前挂错船 → 重路由纠正(连 ship_name/source 一起改)+ 清核对标记。
                conn.execute(
                    f"UPDATE wagon_shipments SET batch_id=?, ship_name=?, "
                    f"source_message_id=?, {_STATUS_SET}{rec_clear} WHERE id=?",
                    [batch_id, ship, r["source_message_id"], *_status_vals(r), rid],
                )
                reroute_n += 1
                touched.add(cur)
                touched.add(batch_id)
            else:
                # 已在正确处,或未命中(grandfather 保留原 batch):仅刷状态。
                conn.execute(
                    f"UPDATE wagon_shipments SET {_STATUS_SET} WHERE id=?",
                    [*_status_vals(r), rid],
                )
                refresh_n += 1
                touched.add(cur)
        else:
            # 全新行。未命中台账 → 暂落兜底船 + 记 WARN(待通知单到达自愈)。
            if not matched:
                warn_new_unmatched.append((t["ydid"], t["car_no"], (t["ticketed_at"] or "")[:10]))
            cols = list(r.keys())
            conn.execute(
                f"INSERT INTO wagon_shipments ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [r[c] for c in cols],
            )
            new_n += 1
            touched.add(batch_id)

    touched.discard("")
    return {
        "new": new_n, "reroute": reroute_n, "refresh": refresh_n,
        "touched": touched, "warn_new_unmatched": warn_new_unmatched,
    }


def recompute_batches(conn: sqlite3.Connection, batch_ids: set[str], now: str) -> dict:
    """对每个被改动的 batch 各自重算 batch_count(=COUNT(DISTINCT ydid),散粮循环车号复用
    不能按 car_no)。装车重量由 main() commit 后按 yaml 规则统一算。"""
    res = {}
    for bid in batch_ids:
        agg = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ydid), "
            "SUM(CASE WHEN dispatch_status='confirmed_received' THEN 1 ELSE 0 END) "
            "FROM wagon_shipments WHERE batch_id=?",
            (bid,),
        ).fetchone()
        conn.execute(
            "UPDATE release_batches SET batch_count=?, updated_at=? WHERE id=?",
            (agg[1], now, bid),
        )
        res[bid] = {"wagons": agg[0], "cars": agg[1], "delivered": agg[2]}
    return res


def main(apply: bool = False) -> None:
    now = now_iso_beijing()
    tickets = fetch_rail_tickets()
    if not tickets:
        print("rail DB 无散粮票")
        return

    conn = sqlite3.connect(str(SOP_DB))
    conn.row_factory = sqlite3.Row
    try:
        ship_batches = load_ship_batches(conn)
        notice_map = load_notice_ship_map(conn)
        fallback_ship = resolve_fallback_ship(conn)
        if fallback_ship not in ship_batches:
            print(f"⚠ 缺 {fallback_ship} lot02 批次(release_batches),中止")
            return
        print(f"未命中台账兜底落点(当前活跃船)={fallback_ship}")
        id2ship = {v: k for k, v in ship_batches.items()}
        routed = resolve_routing(tickets, notice_map, ship_batches, fallback_ship)

        # 预览:按船路由分布
        byship = defaultdict(lambda: [0, 0])  # ship -> [票数, 命中台账数]
        for t, ship, _bid, matched in routed:
            byship[ship][0] += 1
            byship[ship][1] += 1 if matched else 0
        print(f"95306 散粮票 {len(tickets)} 张,按船路由:")
        for ship in sorted(byship):
            n, m = byship[ship]
            print(f"  {ship}: {n} 票(命中通知单台账 {m},未命中 {n - m})")

        stats = upsert_rows(conn, routed, now, ship_batches)
        counts = recompute_batches(conn, stats["touched"], now)

        if stats["warn_new_unmatched"]:
            print(f"\n⚠ WARN:{len(stats['warn_new_unmatched'])} 个**全新且未命中通知单**的散粮车,"
                  f"暂落 {fallback_ship}(请尽快入简装车通知单台账,下趟 sync 自动纠正):")
            for ydid, car, day in stats["warn_new_unmatched"][:20]:
                print(f"    {day}  车号 {car}  ydid {ydid}")

        print(f"\n改动:新增 {stats['new']} / 重路由 {stats['reroute']} / 刷新 {stats['refresh']}")
        for bid in sorted(stats["touched"], key=lambda b: id2ship.get(b, b)):
            c = counts[bid]
            print(f"  [{id2ship.get(bid, bid)}] lot02: {c['cars']} 车(已交付 {c['delivered']})")

        if apply:
            conn.commit()
            from sop_hub.sop.shipped_weight import compute_for_release_batch
            print("\nCOMMIT ✓ 装车重量按 yaml(L70=69/其余=61)分船重算:")
            for bid in sorted(stats["touched"], key=lambda b: id2ship.get(b, b)):
                sw = compute_for_release_batch(bid, db_path=SOP_DB)
                print(f"  [{id2ship.get(bid, bid)}] 已发 {sw.get('shipped_weight_tons')}t,"
                      f"剩余 {sw.get('remaining_weight_tons')}t")
        else:
            conn.rollback()
            print("\nDRY-RUN(加 --apply 提交;装车重量在 --apply 时按 yaml 规则分船计算)")
    finally:
        conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
