"""九三集装箱车票 95306 → sop 同步(多船拆分版,可重复跑,幂等)。

#issue-20260623:原版写死 SHIP="和谐1"+BATCH_ID,诚信集装箱一开发箱子全灌和谐1、
诚信恒0、和谐1超发。与散粮 sync 同病。本版按 **container_loading_notice 台账**
(box 级:ydid+box_no → ship,源自港方逐船货票清单 + 对账脚本)分船路由:

1. 拉 rail DB 高桥镇→新台子 大豆集装箱票(SINCE 起)。
2. 按制票时间聚类发车窗口(间隔 > 2h)。
3. **每个 box 按台账定船**;命中台账 → 该船 lot01;未命中 → 当前活跃船(resolve_default_ship)+ WARN
   (grandfather,等下次对账脚本据新货票清单纠正)。
4. 窗口内按船拆子组,各船子组独立归循环列(ship_scope=该船)。
5. box 行 INSERT OR 自愈:id=sha1(car|box|ydid) 船无关 → 挂错船的箱**重路由纠正**。
6. 各船 batch 重算 cycle / 计数 / shipped_weight。

用法:python scripts/sync_jiusan_harmony_wagons.py
台账由 scripts/reconcile_jiusan_containers.py 据港方货票清单维护。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402
from sop_hub.sop.wagon_container_shipments import split_freight_fee_across_boxes  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
RAIL_DB = Path("/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3")

PROJECT = "jiusan"
# 未命中台账的新箱,只能临时落到**当前未完结 lot**。
# 绝不能再退回已 confirmed_received 的历史船(如和谐1),否则会把新票回刷到旧船。
# 规则:
#   1) 当前唯一 loading 船 → 它
#   2) 否则取最新未完结船(loading/pending_freight/enriched/...)
#   3) 若没有任何未完结船 → 返回空,只告警不硬落
UNFINISHED_STATUSES = (
    "loading",
    "pending_freight",
    "enriched",
    "all_loaded",
    "tracking",
    "delivered",
)
SINCE = "2026-06-09"
WINDOW_GAP_HOURS = 2
OVERLAP_THRESHOLD = 0.6


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def load_ship_batches(conn) -> dict[str, str]:
    """九三各船 lot01 集装箱 batch:ship_name → release_batches.id(单一事实源)。"""
    return {r[1]: r[0] for r in conn.execute(
        "SELECT id, ship_name FROM release_batches "
        "WHERE project=? AND batch_sequence='lot01' AND ship_name IS NOT NULL", (PROJECT,))}


def resolve_default_ship(conn) -> str | None:
    """未命中台账的全新箱临时落点。

    优先唯一 loading 船;否则取最新未完结船。
    若所有 lot01 都已完结(confirmed_received/closed),返回 None:
    这种场景宁可告警等待台账,也不能把新箱硬堆回历史船。
    """
    loading = [r[0] for r in conn.execute(
        "SELECT ship_name FROM release_batches WHERE project=? AND batch_sequence='lot01' "
        "AND dispatch_status='loading' AND ship_name IS NOT NULL", (PROJECT,))]
    if len(loading) == 1:
        return loading[0]
    if len(loading) > 1:
        # A new vessel may be released while an earlier vessel is still loading.
        # Without the box-level ledger, choosing the newest lot silently moves
        # historical circulation into the new vessel. Defer instead.
        return None
    newest_unfinished = conn.execute(
        """
        SELECT ship_name
        FROM release_batches
        WHERE project=? AND batch_sequence='lot01'
          AND ship_name IS NOT NULL
          AND dispatch_status IN ({})
        ORDER BY CASE dispatch_status
          WHEN 'loading' THEN 0
          WHEN 'pending_freight' THEN 1
          WHEN 'enriched' THEN 2
          WHEN 'all_loaded' THEN 3
          WHEN 'tracking' THEN 4
          WHEN 'delivered' THEN 5
          ELSE 9
        END,
        coalesce(dispatch_status_updated_at, updated_at, notice_date, created_at) DESC
        LIMIT 1
        """.format(",".join("?" * len(UNFINISHED_STATUSES))),
        (PROJECT, *UNFINISHED_STATUSES),
    ).fetchone()
    return newest_unfinished[0] if newest_unfinished else None


def load_taizhang(conn) -> dict[tuple[str, str], str]:
    """box 级船归属台账:(ydid, box_no) → ship_name。"""
    try:
        return {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT ydid, box_no, ship_name FROM container_loading_notice")}
    except sqlite3.OperationalError:
        return {}  # 台账表还没建(首次)


def load_car_last_ship(conn) -> dict[str, str]:
    """车号 → 最近一次已入库的船名(循环车体记忆)。

    多船同时 loading 时 default_ship 故意返回 None,避免把整窗硬堆到新船。
    但循环列车体跨趟复用:新票未进台账时,可按该车**上一趟**船归属落库,
    否则 7/21-22 类窗口会整窗跳过,看板/循环列长期卡住。
    时间升序扫一遍,后写覆盖 = 最近一次。
    """
    out: dict[str, str] = {}
    for car, ship in conn.execute(
        "SELECT car_no, ship_name FROM wagon_container_shipments "
        "WHERE project_id=? AND ship_name IS NOT NULL AND ship_name!='' "
        "ORDER BY ticketed_at ASC, ydid ASC",
        (PROJECT,),
    ):
        if car:
            out[str(car)] = str(ship)
    return out


def resolve_box_ship(
    *,
    taizhang: dict[tuple[str, str], str],
    existing: dict[str, str],
    batch_to_ship: dict[str, str],
    car_no: str,
    ydid: str,
    box_no: str,
    default_ship: str | None,
    car_last_ship: dict[str, str] | None = None,
) -> str | None:
    """Resolve one box without allowing a later fallback to reroute history.

    优先级:台账 ydid+box → 已存在同 id 行 → 全局 default_ship → 车体上趟船名。
    """
    ledger_ship = taizhang.get((ydid, box_no))
    if ledger_ship:
        return ledger_ship
    existing_batch = existing.get(stable_hash(car_no, box_no, ydid))
    if existing_batch:
        return batch_to_ship.get(existing_batch)
    if default_ship:
        return default_ship
    if car_last_ship:
        return car_last_ship.get(car_no) or None
    return None


def fetch_rail_tickets() -> list[dict]:
    conn = sqlite3.connect(str(RAIL_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT ydid, czydid, car_no, car_model, container_numbers_json,
                  marked_weight, status_name, latest_stage_key, latest_stage_name,
                  latest_event_time, accepted_at, loaded_at, ticketed_at,
                  departed_at, arrived_at, delivered_at,
                  transport_mode_code, transport_mode_name,
                  origin_name, destination_name, cargo_name
           FROM shipments
           WHERE cargo_name LIKE '%豆%' AND origin_name='高桥镇' AND destination_name='新台子'
             AND transport_mode_name LIKE '%集装箱%' AND ticketed_at >= ?
             AND car_no IS NOT NULL AND car_no != ''
           ORDER BY ticketed_at, car_no""", (SINCE,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_windows(tickets: list[dict]) -> list[list[dict]]:
    from datetime import datetime
    groups, prev = [], None
    for t in tickets:
        dt = datetime.strptime(t["ticketed_at"], "%Y-%m-%d %H:%M:%S")
        if prev is None or (dt - prev).total_seconds() > WINDOW_GAP_HOURS * 3600:
            groups.append([])
        groups[-1].append(t); prev = dt
    return groups


def assign_cycle(conn, cars: set[str], ship: str, first_date: str, now: str) -> str:
    """某船该窗口车号集 vs 该船既有 cycle 重合度归属;无匹配新建。"""
    best_id, best_ratio = None, 0.0
    for cid, in conn.execute(
        "SELECT id FROM cycle_trains WHERE project_id=? AND ship_scope=?", (PROJECT, ship)):
        members = {r[0] for r in conn.execute(
            "SELECT car_no FROM cycle_train_membership WHERE cycle_id=?", (cid,))}
        ratio = len(cars & members) / len(cars) if cars else 0.0
        if ratio > best_ratio:
            best_id, best_ratio = cid, ratio
    if best_id and best_ratio >= OVERLAP_THRESHOLD:
        return best_id
    next_no = conn.execute(
        "SELECT coalesce(max(cycle_no),0)+1 FROM cycle_trains WHERE project_id=? AND ship_scope=?",
        (PROJECT, ship)).fetchone()[0]
    cid = f"jiusan_{ship}_cycle{next_no}_{stable_hash(ship, str(next_no))[:8]}"
    conn.execute(
        """INSERT INTO cycle_trains
           (id, project_id, ship_scope, cycle_name, cycle_no, planned_member_count,
            actual_member_count, expected_box_per_dispatch, first_dispatch_at, last_dispatch_at,
            total_dispatch_count, status, detection_method, detection_confidence, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,0,'active','car_no_signature_inferred',?,?,?)""",
        (cid, PROJECT, ship, f"{ship} {next_no}号列", next_no, len(cars), len(cars), 2,
         first_date, first_date, round(best_ratio, 2), now, now))
    print(f"    新建循环列 {cid}({len(cars)}车,最高重合 {best_ratio:.0%})")
    return cid


def build_box_row(t: dict, box: str, pos: int, ship: str, batch_id: str, cycle_id: str, now: str) -> dict:
    try:
        box_count = len([b for b in json.loads(t.get("container_numbers_json") or "[]") if b]) or 2
    except Exception:
        box_count = 2
    return {
        "id": stable_hash(t["car_no"], box, t["ydid"]),  # 船无关 → 重路由不换 id
        "car_no": t["car_no"], "box_no": box, "box_position": pos,
        "ydid": t["ydid"], "czydid": t["czydid"], "waybill_no": "", "batch_id": batch_id,
        "car_model": t["car_model"] or "",
        "ticketed_at": t["ticketed_at"], "departed_at": t["departed_at"] or "",
        "arrived_at": t["arrived_at"] or "", "delivered_at": t["delivered_at"] or "",
        "accepted_at": t["accepted_at"] or "", "loaded_at": t["loaded_at"] or "",
        "status_name": t["status_name"] or "", "latest_stage_key": t["latest_stage_key"] or "",
        "latest_stage_name": t["latest_stage_name"] or "", "latest_event_time": t["latest_event_time"] or "",
        "origin_name": t["origin_name"], "destination_name": t["destination_name"],
        "transport_mode_code": t["transport_mode_code"], "transport_mode_name": t["transport_mode_name"],
        "cargo_name": t["cargo_name"], "marked_weight": t["marked_weight"],
        "freight_fee": split_freight_fee_across_boxes(t.get("freight_fee"), box_count),
        "project_id": PROJECT, "ship_name": ship,
        "consignor": "锦州港物流发展有限公司", "consignee": "国家粮食和物资储备局辽宁局三三0处",
        "dispatch_status": "loading",
        "source_message_id": f"auto_jiusan_sync_{t['ticketed_at'][:10]}", "source_group_id": "",
        "created_at": now, "updated_at": now, "cycle_id": cycle_id,
    }


def _has_col(conn, table, col):
    return col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def upsert_rows(conn, rows: list[dict], existing: dict[str, str]) -> tuple[int, int, int]:
    """existing: id → 当前 batch_id(全 jiusan lot01)。返回 (新增, 刷新, 重路由纠正)。"""
    new_n = refresh_n = reroute_n = 0
    # 归属变了 → 清核对完毕标记(否则 reconcile 把它当已核对剔除,脏标记卡死)
    rec_clear = (", reconciled_at=NULL, reconcile_source_ref=NULL"
                 if _has_col(conn, "wagon_container_shipments", "reconciled_at") else "")
    for r in rows:
        rid = r["id"]
        if rid in existing:
            if existing[rid] != r["batch_id"]:
                # 挂错船 → 重路由纠正(自愈)+ 刷状态 + 清核对完毕标记
                conn.execute(
                    f"""UPDATE wagon_container_shipments SET batch_id=?, ship_name=?,
                       status_name=?, latest_stage_key=?, latest_stage_name=?, latest_event_time=?,
                       departed_at=?, arrived_at=?, delivered_at=?, cycle_id=?, freight_fee=?, updated_at=?{rec_clear}
                       WHERE id=?""",
                    (r["batch_id"], r["ship_name"], r["status_name"], r["latest_stage_key"],
                     r["latest_stage_name"], r["latest_event_time"], r["departed_at"], r["arrived_at"],
                     r["delivered_at"], r["cycle_id"], r["freight_fee"], r["updated_at"], rid))
                existing[rid] = r["batch_id"]; reroute_n += 1
            else:
                conn.execute(
                    """UPDATE wagon_container_shipments SET status_name=?, latest_stage_key=?,
                       latest_stage_name=?, latest_event_time=?, departed_at=?, arrived_at=?,
                       delivered_at=?, freight_fee=?, updated_at=? WHERE id=?""",
                    (r["status_name"], r["latest_stage_key"], r["latest_stage_name"],
                     r["latest_event_time"], r["departed_at"], r["arrived_at"], r["delivered_at"],
                     r["freight_fee"], r["updated_at"], rid)); refresh_n += 1
        else:
            cols = list(r.keys())
            conn.execute(f"INSERT INTO wagon_container_shipments ({','.join(cols)}) "
                         f"VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
            existing[rid] = r["batch_id"]; new_n += 1
    return new_n, refresh_n, reroute_n


def recompute_counters(conn, ship_batches: dict[str, str], now: str) -> None:
    for ship in ship_batches:
        for cid, in conn.execute(
            "SELECT id FROM cycle_trains WHERE project_id=? AND ship_scope=?", (PROJECT, ship)):
            for car, n_disp, first_d, last_d in conn.execute(
                """SELECT car_no, count(DISTINCT date(ticketed_at)), min(date(ticketed_at)),
                          max(date(ticketed_at)) FROM wagon_container_shipments WHERE cycle_id=?
                   GROUP BY car_no""", (cid,)).fetchall():
                conn.execute(
                    """INSERT INTO cycle_train_membership
                       (id, cycle_id, car_no, joined_at, dispatch_count, last_dispatch_at,
                        member_status, source, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,'active','auto_inferred_from_95306',?,?)
                       ON CONFLICT(cycle_id, car_no) DO UPDATE SET
                         dispatch_count=excluded.dispatch_count,
                         last_dispatch_at=excluded.last_dispatch_at, updated_at=excluded.updated_at""",
                    (stable_hash(cid, car), cid, car, first_d, n_disp, last_d, now, now))
            agg = conn.execute(
                """SELECT count(DISTINCT date(ticketed_at)), min(date(ticketed_at)),
                          max(date(ticketed_at)), count(DISTINCT car_no)
                   FROM wagon_container_shipments WHERE cycle_id=?""", (cid,)).fetchone()
            if agg and agg[0]:
                conn.execute(
                    """UPDATE cycle_trains SET total_dispatch_count=?, first_dispatch_at=?,
                       last_dispatch_at=?, actual_member_count=?, updated_at=? WHERE id=?""",
                    (agg[0], agg[1], agg[2], agg[3], now, cid))
        bid = ship_batches[ship]
        n_ydid = conn.execute("SELECT count(DISTINCT ydid) FROM wagon_container_shipments WHERE batch_id=?",
                              (bid,)).fetchone()[0]
        conn.execute("UPDATE release_batches SET batch_count=?, updated_at=? WHERE id=?", (n_ydid, now, bid))


def main() -> None:
    now = now_iso_beijing()
    tickets = fetch_rail_tickets()
    if not tickets:
        print("rail DB 无票"); return
    windows = group_windows(tickets)
    print(f"95306 票 {len(tickets)} 张 → {len(windows)} 个发车窗口")

    conn = sqlite3.connect(str(SOP_DB))
    try:
        ship_batches = load_ship_batches(conn)
        batch_to_ship = {batch_id: ship for ship, batch_id in ship_batches.items()}
        taizhang = load_taizhang(conn)
        default_ship = resolve_default_ship(conn)
        car_last_ship = load_car_last_ship(conn)
        existing = {r[0]: r[1] for r in conn.execute(
            "SELECT id, batch_id FROM wagon_container_shipments WHERE batch_id IN ({})".format(
                ",".join("?" * len(ship_batches))), list(ship_batches.values()))}
        print(f"船 lot01: {list(ship_batches)} | 台账 {len(taizhang)} 箱 | 现有 {len(existing)} 箱 "
              f"| 兜底落点(当前未完结船)={default_ship or '无'} "
              f"| 车体上趟记忆 {len(car_last_ship)} 车")
        if default_ship is not None and default_ship not in ship_batches:
            print(f"!! 兜底船 {default_ship} 无 lot01 batch,退出"); return

        tot_new = tot_ref = tot_rr = 0
        unmatched_boxes = 0
        skipped_unmatched_boxes = 0
        history_fallback_boxes = 0
        for w in windows:
            # 1) 窗口内逐 box 定船:台账 → 已存在行 → default → 车体上趟
            ship_boxes: dict[str, list[tuple]] = defaultdict(list)  # ship → [(t, box, pos)]
            for t in w:
                for pos, box in enumerate(json.loads(t["container_numbers_json"] or "[]"), 1):
                    ship = resolve_box_ship(
                        taizhang=taizhang,
                        existing=existing,
                        batch_to_ship=batch_to_ship,
                        car_no=t["car_no"],
                        ydid=t["ydid"],
                        box_no=box,
                        default_ship=default_ship,
                        car_last_ship=car_last_ship,
                    )
                    if ship is None:
                        unmatched_boxes += 1
                        skipped_unmatched_boxes += 1
                        continue
                    # 统计:无台账/无 default 时靠车体上趟
                    if (
                        (t["ydid"], box) not in taizhang
                        and stable_hash(t["car_no"], box, t["ydid"]) not in existing
                        and not default_ship
                        and car_last_ship.get(t["car_no"]) == ship
                    ):
                        history_fallback_boxes += 1
                    ship_boxes[ship].append((t, box, pos))
            # 2) 各船子组:归循环列 + 建行 + upsert
            for ship, items in ship_boxes.items():
                if ship not in ship_batches:
                    print(f"  !! 台账船 {ship} 无 lot01 batch,跳过 {len(items)} 箱"); continue
                cars = {it[0]["car_no"] for it in items}
                cid = assign_cycle(conn, cars, ship, w[0]["ticketed_at"][:10], now)
                rows = [build_box_row(t, box, pos, ship, ship_batches[ship], cid, now)
                        for (t, box, pos) in items]
                n, rf, rr = upsert_rows(conn, rows, existing)
                tot_new += n; tot_ref += rf; tot_rr += rr
                # 新入库后刷新车体记忆,供同次跑的后续窗口使用
                for t, _box, _pos in items:
                    car_last_ship[t["car_no"]] = ship
            print(f"  窗口 {w[0]['ticketed_at']}~{w[-1]['ticketed_at']} "
                  f"({len(w)}车) 船分布={ {s: len(v) for s, v in ship_boxes.items()} }")
        recompute_counters(conn, ship_batches, now)
        from sop_hub.sop.jiusan_cycle_pool import reconcile_recent_cycle_pool
        cycle_pool = reconcile_recent_cycle_pool(
            conn,
            since=now[:10],
            now=now,
        )
        conn.commit()
        print(f"COMMIT ✓ 新增 {tot_new} / 刷新 {tot_ref} / 重路由纠正 {tot_rr} box")
        if cycle_pool["attached_to_existing_cycle"] or cycle_pool["created_new_cycle"]:
            print(
                "  循环车体池: "
                f"补既有列 {len(cycle_pool['attached_to_existing_cycle'])}车 / "
                f"新列 {len(cycle_pool['created_new_cycle'])}车"
            )
        if history_fallback_boxes:
            print(f"  车体上趟归属补入: {history_fallback_boxes} box "
                  f"(多船 loading 无台账时的循环车体回退)")
        if unmatched_boxes:
            print(f"⚠️ 仍无法定船(无台账/无 default/无上趟记忆): {unmatched_boxes} box "
                  f"(已跳过 {skipped_unmatched_boxes}) —— 待港方货票清单后再入库")
        elif default_ship and history_fallback_boxes == 0:
            pass  # 安静:全走台账或唯一 default
        from sop_hub.sop.shipped_weight import compute_for_release_batch
        for ship, bid in ship_batches.items():
            sw = compute_for_release_batch(bid, db_path=SOP_DB)
            n = conn.execute("SELECT count(*) FROM wagon_container_shipments WHERE batch_id=?", (bid,)).fetchone()[0]
            print(f"  {ship} lot01: {n}箱 已发 {sw.get('shipped_weight_tons')}t / 剩 {sw.get('remaining_weight_tons')}t")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
