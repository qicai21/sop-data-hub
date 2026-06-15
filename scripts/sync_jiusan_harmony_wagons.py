"""九三和谐1 集装箱车票 95306 → sop 同步(可重复跑,幂等)。

每天循环列发车后 95306 出新票,跑本脚本即可:
1. 拉 rail DB 高桥镇→新台子 大豆集装箱票(2026-06-09 起)
2. 按制票时间聚类成"发车窗口"(间隔 > 2h 分组)
3. 每窗口按车号集合与既有 cycle 成员重合度(>=60%)归属循环列;无匹配则新建列
4. box 行 INSERT OR REPLACE(同 hash 键,顺带刷新已有行的 95306 状态)
5. 从 wagon 行重算 cycle_trains / cycle_train_membership / release_batch 计数

用法:python scripts/sync_jiusan_harmony_wagons.py
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

PROJECT = "jiusan"
SHIP = "和谐1"
BATCH_ID = "e96f4b3b83c74b4891c6b0957f6989bb827de45b"  # jiusan|和谐1|lot01|2026-06-09
SINCE = "2026-06-09"
WINDOW_GAP_HOURS = 2
OVERLAP_THRESHOLD = 0.6
# 装车重量(28.4/箱)已收口到 config/project_sops/jiusan.yaml 的 shipped_weight_rule,
# 本脚本不再硬算,落库后统一调 compute_for_release_batch(单一真相)。


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def fetch_rail_tickets() -> list[dict]:
    conn = sqlite3.connect(str(RAIL_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ydid, czydid, car_no, car_model, container_numbers_json,
               marked_weight, status_name, latest_stage_key, latest_stage_name,
               latest_event_time, accepted_at, loaded_at, ticketed_at,
               departed_at, arrived_at, delivered_at,
               transport_mode_code, transport_mode_name,
               origin_name, destination_name, cargo_name
        FROM shipments
        WHERE cargo_name LIKE '%豆%'
          AND origin_name = '高桥镇'
          AND destination_name = '新台子'
          AND transport_mode_name LIKE '%集装箱%'
          AND ticketed_at >= ?
          AND car_no IS NOT NULL AND car_no != ''
        ORDER BY ticketed_at, car_no
        """,
        (SINCE,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def group_windows(tickets: list[dict]) -> list[list[dict]]:
    """按 ticketed_at 升序,间隔 > WINDOW_GAP_HOURS 切组。"""
    from datetime import datetime

    groups: list[list[dict]] = []
    prev_dt = None
    for t in tickets:
        dt = datetime.strptime(t["ticketed_at"], "%Y-%m-%d %H:%M:%S")
        if prev_dt is None or (dt - prev_dt).total_seconds() > WINDOW_GAP_HOURS * 3600:
            groups.append([])
        groups[-1].append(t)
        prev_dt = dt
    return groups


def assign_cycle(conn: sqlite3.Connection, window: list[dict], now: str) -> str:
    """车号集合 vs 既有 cycle 成员重合度归属;无匹配新建列。"""
    cars = {t["car_no"] for t in window}
    best_id, best_ratio = None, 0.0
    for cid, in conn.execute(
        "SELECT id FROM cycle_trains WHERE project_id=? AND ship_scope=?",
        (PROJECT, SHIP),
    ):
        members = {r[0] for r in conn.execute(
            "SELECT car_no FROM cycle_train_membership WHERE cycle_id=?", (cid,))}
        ratio = len(cars & members) / len(cars) if cars else 0.0
        if ratio > best_ratio:
            best_id, best_ratio = cid, ratio
    if best_id and best_ratio >= OVERLAP_THRESHOLD:
        return best_id

    next_no = (conn.execute(
        "SELECT coalesce(max(cycle_no),0)+1 FROM cycle_trains WHERE project_id=? AND ship_scope=?",
        (PROJECT, SHIP),
    ).fetchone()[0])
    cid = f"jiusan_{SHIP}_cycle{next_no}_{stable_hash(SHIP, str(next_no))[:8]}"
    first_date = window[0]["ticketed_at"][:10]
    conn.execute(
        """INSERT INTO cycle_trains
           (id, project_id, ship_scope, cycle_name, cycle_no,
            planned_member_count, actual_member_count, expected_box_per_dispatch,
            first_dispatch_at, last_dispatch_at, total_dispatch_count, status,
            detection_method, detection_confidence, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,0,'active','car_no_signature_inferred',?,?,?)""",
        (cid, PROJECT, SHIP, f"{SHIP} {next_no}号列", next_no,
         len(cars), len(cars), 2, first_date, first_date,
         round(best_ratio, 2), now, now),
    )
    print(f"  新建循环列 {cid}({len(cars)} 车,与既有列最高重合 {best_ratio:.0%})")
    return cid


def build_box_rows(window: list[dict], cycle_id: str, now: str) -> list[dict]:
    rows = []
    for t in window:
        boxes = json.loads(t["container_numbers_json"] or "[]")
        label = f"auto_harmony1_sync_{t['ticketed_at'][:10]}"
        for pos, box in enumerate(boxes, start=1):
            rows.append({
                "id": stable_hash(t["car_no"], box, t["ydid"]),
                "car_no": t["car_no"], "box_no": box, "box_position": pos,
                "ydid": t["ydid"], "czydid": t["czydid"], "waybill_no": "",
                "batch_id": BATCH_ID,
                "car_model": t["car_model"] or "",
                "ticketed_at": t["ticketed_at"], "departed_at": t["departed_at"] or "",
                "arrived_at": t["arrived_at"] or "", "delivered_at": t["delivered_at"] or "",
                "accepted_at": t["accepted_at"] or "", "loaded_at": t["loaded_at"] or "",
                "status_name": t["status_name"] or "",
                "latest_stage_key": t["latest_stage_key"] or "",
                "latest_stage_name": t["latest_stage_name"] or "",
                "latest_event_time": t["latest_event_time"] or "",
                "origin_name": t["origin_name"], "destination_name": t["destination_name"],
                "transport_mode_code": t["transport_mode_code"],
                "transport_mode_name": t["transport_mode_name"],
                "cargo_name": t["cargo_name"],
                "marked_weight": t["marked_weight"],
                "project_id": PROJECT, "ship_name": SHIP,
                "consignor": "锦州港物流发展有限公司",
                "consignee": "国家粮食和物资储备局辽宁局三三0处",
                "dispatch_status": "loading",
                "source_message_id": label, "source_group_id": "",
                "created_at": now, "updated_at": now,
                "cycle_id": cycle_id,
            })
    return rows


def upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    existing = {r[0] for r in conn.execute(
        "SELECT id FROM wagon_container_shipments WHERE batch_id=?", (BATCH_ID,))}
    new_n = sum(1 for r in rows if r["id"] not in existing)
    for r in rows:
        if r["id"] in existing:
            # 已有行只刷 95306 状态字段,保留原 created_at/source_message_id/cycle_id
            conn.execute(
                """UPDATE wagon_container_shipments SET
                   status_name=?, latest_stage_key=?, latest_stage_name=?,
                   latest_event_time=?, departed_at=?, arrived_at=?, delivered_at=?,
                   updated_at=? WHERE id=?""",
                (r["status_name"], r["latest_stage_key"], r["latest_stage_name"],
                 r["latest_event_time"], r["departed_at"], r["arrived_at"],
                 r["delivered_at"], r["updated_at"], r["id"]),
            )
        else:
            cols = list(r.keys())
            conn.execute(
                f"INSERT INTO wagon_container_shipments ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [r[c] for c in cols],
            )
    return new_n, len(rows) - new_n


def recompute_counters(conn: sqlite3.Connection, now: str) -> None:
    # membership:每车在该 cycle 的发运日集合
    for cid, in conn.execute(
        "SELECT id FROM cycle_trains WHERE project_id=? AND ship_scope=?",
        (PROJECT, SHIP),
    ):
        per_car = conn.execute(
            """SELECT car_no, count(DISTINCT date(ticketed_at)), min(date(ticketed_at)), max(date(ticketed_at))
               FROM wagon_container_shipments WHERE cycle_id=? GROUP BY car_no""",
            (cid,),
        ).fetchall()
        for car, n_disp, first_d, last_d in per_car:
            conn.execute(
                """INSERT INTO cycle_train_membership
                   (id, cycle_id, car_no, joined_at, dispatch_count, last_dispatch_at,
                    member_status, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,'active','auto_inferred_from_95306',?,?)
                   ON CONFLICT(cycle_id, car_no) DO UPDATE SET
                     dispatch_count=excluded.dispatch_count,
                     last_dispatch_at=excluded.last_dispatch_at,
                     updated_at=excluded.updated_at""",
                (stable_hash(cid, car), cid, car, first_d, n_disp, last_d, now, now),
            )
        agg = conn.execute(
            """SELECT count(DISTINCT date(ticketed_at)), min(date(ticketed_at)),
                      max(date(ticketed_at)), count(DISTINCT car_no)
               FROM wagon_container_shipments WHERE cycle_id=?""",
            (cid,),
        ).fetchone()
        if agg and agg[0]:
            conn.execute(
                """UPDATE cycle_trains SET total_dispatch_count=?, first_dispatch_at=?,
                   last_dispatch_at=?, actual_member_count=?, updated_at=? WHERE id=?""",
                (agg[0], agg[1], agg[2], agg[3], now, cid),
            )

    n_ydid, n_box = conn.execute(
        "SELECT count(DISTINCT ydid), count(*) FROM wagon_container_shipments WHERE batch_id=?",
        (BATCH_ID,),
    ).fetchone()
    # batch_count 与车票数同步;装车重量 / actual_wagon_count 走统一 yaml 规则,
    # 由 main() commit 后调 compute_for_release_batch 落,本处不再硬算。
    conn.execute(
        "UPDATE release_batches SET batch_count=?, updated_at=? WHERE id=?",
        (n_ydid, now, BATCH_ID),
    )
    print(f"  release_batch 计数:{n_ydid} 车票 / {n_box} box")


def main() -> None:
    now = now_iso_beijing()
    tickets = fetch_rail_tickets()
    if not tickets:
        print("rail DB 无票")
        return
    windows = group_windows(tickets)
    print(f"95306 票 {len(tickets)} 张 → {len(windows)} 个发车窗口")

    conn = sqlite3.connect(str(SOP_DB))
    try:
        total_new = total_refresh = 0
        for w in windows:
            cid = assign_cycle(conn, w, now)
            rows = build_box_rows(w, cid, now)
            new_n, ref_n = upsert_rows(conn, rows)
            total_new += new_n
            total_refresh += ref_n
            print(f"  窗口 {w[0]['ticketed_at']} ~ {w[-1]['ticketed_at']}"
                  f"({len(w)} 车)→ {cid.split('_')[2]}  新增 {new_n} box / 刷新 {ref_n} box")
        recompute_counters(conn, now)
        conn.commit()
        print(f"COMMIT ✓ 新增 {total_new} box,刷新 {total_refresh} box")
        # 装车重量统一走 yaml shipped_weight_rule(独立连接,需先 commit)
        from sop_hub.sop.shipped_weight import compute_for_release_batch
        sw = compute_for_release_batch(BATCH_ID, db_path=SOP_DB)
        print(f"  装车重量(yaml container 28.4/箱):已发 {sw.get('shipped_weight_tons')}t / "
              f"剩余 {sw.get('remaining_weight_tons')}t")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
