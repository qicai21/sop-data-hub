"""一次性脚本:从 wagon_container_shipments 反推九三循环列 → 落 cycle_trains。

识别算法:同船 unique car_no 按"发运日签名"(GROUP_CONCAT distinct ticketed_at)
分组。一个签名内 >= 10 辆车 = 一支识别出的循环列。

实证参考:data/contracts/jiusan_soybean/cycle_freight_observation_v1.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"

MIN_MEMBERS = 10  # 编组规模阈值(< 10 = 散车)


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def detect_cycles_for_ship(conn, project_id: str, ship_name: str) -> list[dict]:
    """对同船所有 car 算发运日签名,signature 聚成 cycle。"""
    cur = conn.cursor()
    cur.execute("""
        SELECT car_no, GROUP_CONCAT(DISTINCT substr(ticketed_at,1,10)) AS dates,
               COUNT(DISTINCT ydid) AS trips
        FROM wagon_container_shipments
        WHERE project_id=? AND ship_name=?
        GROUP BY car_no
    """, (project_id, ship_name))

    by_sig: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
    for cn, dates, trips in cur.fetchall():
        sig = tuple(sorted(dates.split(",")))
        by_sig[sig].append((cn, trips))

    cycles = []
    cycle_no = 0
    # 排序:成员多的优先
    for sig, members in sorted(by_sig.items(), key=lambda kv: -len(kv[1])):
        if len(members) < MIN_MEMBERS:
            continue
        cycle_no += 1
        intervals = [
            (date.fromisoformat(sig[i + 1]) - date.fromisoformat(sig[i])).days
            for i in range(len(sig) - 1)
        ]
        avg_period = sum(intervals) / len(intervals) if intervals else None
        first_d, last_d = sig[0], sig[-1]
        total_dispatch = len(sig)

        cycle_id = stable_hash(project_id, ship_name, str(cycle_no))[:16]
        cycle_id_full = f"{project_id}_{ship_name}_cycle{cycle_no}_{cycle_id[:8]}"
        # 单日发运不算真循环列,标为 single_dispatch
        status = "single_dispatch" if total_dispatch == 1 else "active"

        cycles.append({
            "id": cycle_id_full,
            "cycle_no": cycle_no,
            "members": members,  # [(car_no, trips), ...]
            "signature": list(sig),
            "first_d": first_d,
            "last_d": last_d,
            "avg_period": avg_period,
            "total_dispatch": total_dispatch,
            "status": status,
        })
    return cycles


def seed_cycle(conn, cycles: list[dict], project_id: str, ship_name: str,
               parallel_count: int, now: str):
    cur = conn.cursor()
    for c in cycles:
        sig_str = ",".join(c["signature"])
        # 一次发车带几个箱:集装箱默认 2 箱/车 × 编组规模
        expected_box_per_dispatch = len(c["members"]) * 2
        # 此列预期占用箱池:列每次循环带 ~100 箱(50 车 × 2),按循环周期同时在用
        # 经验:50 车列在循环中(港+路+330)瞬时持有 ~100 箱
        expected_box_pool = expected_box_per_dispatch

        cur.execute("""
            INSERT OR REPLACE INTO cycle_trains (
                id, project_id, ship_scope, cycle_name, cycle_no,
                period_days, planned_member_count, actual_member_count,
                parallel_offset_days,
                expected_box_per_dispatch, expected_box_pool_size,
                first_dispatch_at, last_dispatch_at, total_dispatch_count,
                status, detection_method, detection_signature, detection_confidence,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c["id"], project_id, ship_name,
            f"{ship_name} 列{c['cycle_no']}",
            c["cycle_no"],
            c["avg_period"],
            50,  # planned_member_count 经验值
            len(c["members"]),
            1,  # parallel_offset_days
            expected_box_per_dispatch, expected_box_pool,
            c["first_d"], c["last_d"], c["total_dispatch"],
            c["status"],
            "car_no_signature_inferred", sig_str, 0.95,
            None, now, now,
        ))

        # membership
        for car_no, trips in c["members"]:
            mid = stable_hash(c["id"], car_no)
            # 查这辆车在此 cycle 的首末发运日
            cur.execute("""
                SELECT MIN(substr(ticketed_at,1,10)), MAX(substr(ticketed_at,1,10))
                FROM wagon_container_shipments
                WHERE project_id=? AND ship_name=? AND car_no=?
            """, (project_id, ship_name, car_no))
            joined_d, last_d = cur.fetchone()
            personal_period = c["avg_period"]
            cur.execute("""
                INSERT OR REPLACE INTO cycle_train_membership (
                    id, cycle_id, car_no, joined_at, left_at,
                    dispatch_count, last_dispatch_at,
                    avg_personal_period_days, member_status, role,
                    source, source_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mid, c["id"], car_no, joined_d, last_d,
                trips, last_d,
                personal_period, "active",
                "core" if c["cycle_no"] <= 3 else "swing",
                "auto_inferred_from_95306",
                f"detected from {ship_name} on {now[:10]}",
                now, now,
            ))

        # 反写 wagon_container_shipments.cycle_id
        car_nos = [m[0] for m in c["members"]]
        sig_dates = c["signature"]
        for car_no in car_nos:
            for d_ in sig_dates:
                cur.execute("""
                    UPDATE wagon_container_shipments
                    SET cycle_id=?
                    WHERE project_id=? AND ship_name=? AND car_no=?
                      AND substr(ticketed_at,1,10)=?
                """, (c["id"], project_id, ship_name, car_no, d_))


def main(apply: bool = False):
    now = now_iso_beijing()
    conn = sqlite3.connect(str(SOP_DB))

    all_cycles = {}
    for ship in ["昆娜", "玛格丽特"]:
        cycles = detect_cycles_for_ship(conn, "jiusan", ship)
        print(f"\n=== {ship} 识别 {len(cycles)} 个 cycle(>= {MIN_MEMBERS} 辆) ===")
        for c in cycles:
            period_str = f"{c['avg_period']:.1f}d" if c['avg_period'] is not None else "n/a"
            print(f"  列{c['cycle_no']}: {len(c['members']):3d} 辆  发运日 {c['signature']}  "
                  f"周期 {period_str}  循环 {c['total_dispatch']} 次")
        seed_cycle(conn, cycles, "jiusan", ship, parallel_count=len(cycles), now=now)
        all_cycles[ship] = cycles

    if apply:
        conn.commit()
        print("\nCOMMIT ✓")
    else:
        conn.rollback()
        print("\nDRY-RUN(加 --apply 真正提交)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    main(apply=args.apply)
