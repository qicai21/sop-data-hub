"""车体池台账(九三循环列等):跟踪每节车皮在循环池里的进出。

业务逻辑(用户口径):
- 每列发出后,对照实际发运车号复核车体池。
- 新车号 → 加入池(active)。
- 某车从「本列下一趟」缺失 → 标「待转出」(pending_out)。
- 当 1/2/3 号列后续趟次都确认没有这节车 → 「彻底转出」(transferred_out)。

判定全部从 wagon_container_shipments(95306 背书的实发车号)推,幂等可重复跑:
  car.last_seen = 最后一次出现的趟次日期
  本列在 last_seen 之后又发过 → 本列把它甩了 → pending_out
  每一个 active 列都在 last_seen 之后发过(都没带它)→ transferred_out
  否则 active

用法:
  python scripts/wagon_body_pool.py                  # reconcile + show(干跑)
  python scripts/wagon_body_pool.py --apply          # 提交
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
PROJECT = "jiusan"
SHIP = "和谐1"


def stable_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wagon_body_pool (
          id TEXT PRIMARY KEY,
          project TEXT NOT NULL, ship_name TEXT NOT NULL,
          car_no TEXT NOT NULL, car_model TEXT DEFAULT '',
          home_cycle_no INTEGER,
          status TEXT NOT NULL DEFAULT 'active',
          first_seen_date TEXT, last_seen_date TEXT,
          last_seen_cycle_no INTEGER, dispatch_count INTEGER DEFAULT 0,
          pending_since TEXT, transferred_at TEXT, note TEXT DEFAULT '',
          created_at TEXT, updated_at TEXT,
          UNIQUE(project, ship_name, car_no)
        )
    """)
    conn.commit()


def load_dispatches(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """返回 (per_car, cycle_trips)。
    per_car[car_no] = {model, appearances:[(cyc_no,date)], first, last, last_cyc, n}
    cycle_trips[cyc_no] = sorted set of trip dates
    """
    rows = conn.execute("""
        SELECT ct.cycle_no, date(w.ticketed_at) d, w.car_no,
               max(w.car_model) car_model
        FROM wagon_container_shipments w
        JOIN cycle_trains ct ON ct.id = w.cycle_id
        WHERE ct.project_id=? AND ct.ship_scope=? AND w.car_no IS NOT NULL AND w.car_no!=''
        GROUP BY ct.cycle_no, date(w.ticketed_at), w.car_no
    """, (PROJECT, SHIP)).fetchall()
    per_car: dict = {}
    cycle_trips: dict = defaultdict(set)
    for cyc, d, car, model in rows:
        cycle_trips[cyc].add(d)
        c = per_car.setdefault(car, {"model": "", "app": []})
        c["app"].append((cyc, d))
        if model and not c["model"]:
            c["model"] = model
    for car, c in per_car.items():
        app = sorted(c["app"], key=lambda x: x[1])
        c["first"] = app[0][1]
        c["last"] = app[-1][1]
        c["last_cyc"] = app[-1][0]
        c["home_cyc"] = app[-1][0]   # 当前归属 = 最后出现的列
        c["n"] = len({d for _, d in app})
    return per_car, dict(cycle_trips)


def classify(car: dict, cycle_trips: dict) -> str:
    ls = car["last"]
    home = car["home_cyc"]
    # 本列在 last_seen 之后又发过(没带它)
    home_dropped = any(d > ls for d in cycle_trips.get(home, set()))
    # 每个 active 列都在 last_seen 之后发过(都没带它)→ 彻底转出
    confirmed_gone = all(
        any(d > ls for d in dates) for dates in cycle_trips.values()
    )
    if confirmed_gone:
        return "transferred_out"
    if home_dropped:
        return "pending_out"
    return "active"


def reconcile(conn: sqlite3.Connection, now: str) -> dict:
    per_car, cycle_trips = load_dispatches(conn)
    existing = {r[0]: dict(zip(("status", "pending_since", "transferred_at"), r[1:]))
                for r in conn.execute(
                    "SELECT car_no, status, pending_since, transferred_at "
                    "FROM wagon_body_pool WHERE project=? AND ship_name=?",
                    (PROJECT, SHIP))}
    counts = defaultdict(int)
    transitions = []
    for car_no, c in per_car.items():
        status = classify(c, cycle_trips)
        counts[status] += 1
        prev = existing.get(car_no, {})
        prev_status = prev.get("status")
        pending_since = prev.get("pending_since")
        transferred_at = prev.get("transferred_at")
        if status == "pending_out" and not pending_since:
            pending_since = c["last"]
        if status == "transferred_out" and not transferred_at:
            transferred_at = now
        if prev_status and prev_status != status:
            transitions.append((car_no, prev_status, status))
        rid = stable_hash(PROJECT, SHIP, car_no)
        conn.execute("""
            INSERT INTO wagon_body_pool
              (id, project, ship_name, car_no, car_model, home_cycle_no, status,
               first_seen_date, last_seen_date, last_seen_cycle_no, dispatch_count,
               pending_since, transferred_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project, ship_name, car_no) DO UPDATE SET
              car_model=excluded.car_model, home_cycle_no=excluded.home_cycle_no,
              status=excluded.status, last_seen_date=excluded.last_seen_date,
              last_seen_cycle_no=excluded.last_seen_cycle_no,
              dispatch_count=excluded.dispatch_count,
              pending_since=excluded.pending_since,
              transferred_at=excluded.transferred_at, updated_at=excluded.updated_at
        """, (rid, PROJECT, SHIP, car_no, c["model"], c["home_cyc"], status,
              c["first"], c["last"], c["last_cyc"], c["n"],
              pending_since, transferred_at, now, now))
    return {"counts": dict(counts), "total": len(per_car),
            "transitions": transitions, "cycle_trips": cycle_trips}


def show(conn: sqlite3.Connection) -> None:
    print(f"车体池 — {PROJECT} / {SHIP}")
    for st, label in [("active", "在池"), ("pending_out", "待转出"),
                      ("transferred_out", "已转出")]:
        rows = conn.execute(
            "SELECT car_no, car_model, home_cycle_no, last_seen_date, dispatch_count "
            "FROM wagon_body_pool WHERE project=? AND ship_name=? AND status=? "
            "ORDER BY home_cycle_no, car_no", (PROJECT, SHIP, st)).fetchall()
        print(f"  [{label}] {len(rows)} 辆")
        if st != "active" and rows:        # 异常态逐辆列出
            for car, model, cyc, ls, n in rows:
                print(f"      {car}({model}) {cyc}号列 末见{ls} 发{n}趟")
    # 按列分布
    print("  按号列分布(在池):")
    for cyc, n, models in conn.execute(
        "SELECT home_cycle_no, count(*), group_concat(DISTINCT car_model) "
        "FROM wagon_body_pool WHERE project=? AND ship_name=? AND status='active' "
        "GROUP BY home_cycle_no ORDER BY home_cycle_no", (PROJECT, SHIP)):
        print(f"      {cyc}号列: {n} 辆  [{models}]")


def main(apply: bool = False) -> None:
    now = now_iso_beijing()
    conn = sqlite3.connect(str(SOP_DB))
    ensure_schema(conn)
    try:
        res = reconcile(conn, now)
        if apply:
            conn.commit(); tag = "COMMIT ✓"
        else:
            tag = "DRY-RUN(加 --apply 提交)"
        print(f"{tag} 车体池 {res['total']} 辆  状态:{res['counts']}")
        for car, a, b in res["transitions"]:
            print(f"  ⚠ 状态变更 {car}: {a} → {b}")
        print()
        show(conn)
        if not apply:
            conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
