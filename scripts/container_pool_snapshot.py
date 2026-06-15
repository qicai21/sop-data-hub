"""集装箱池每日快照台账(九三大豆等循环列项目)。

为什么要它:wagon_container_shipments 只记「已发运重箱」(去程/到站/交付),
池子里的空箱、港内待发重箱、330 线上/落地库存、反空在途 —— 这些节点库存
原来只在晨报 markdown 里,非结构化、不可查。本表把每日各节点箱数落库,
形成可追踪、可守恒校验的箱池台账。

每天晨报核准后跑一次记一行;dashboard / 趋势分析直接读本表。

用法:
  python scripts/container_pool_snapshot.py --show                 # 看最近快照
  python scripts/container_pool_snapshot.py --record 2026-06-15 \
      --port-loaded 65 --port-empty 147 --port-empty-prep 98 \
      --transit-loaded 100 --transit-empty 100 \
      --line330-empty 14 --ground330-loaded 100 \
      --inferred transit_empty --note "D6 现场核准" --apply
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"

# 节点字段(重箱 + 空箱),全部 INT,缺省 0
LOADED_FIELDS = ["port_loaded", "transit_loaded", "xtz_loaded",
                 "line330_loaded", "ground330_loaded"]
EMPTY_FIELDS = ["port_empty", "port_empty_prep", "transit_empty",
                "xtz_empty", "line330_empty", "ground330_empty"]
NODE_FIELDS = LOADED_FIELDS + EMPTY_FIELDS

FIELD_LABELS = {
    "port_loaded": "港内待发重箱", "port_empty": "港内可用空箱",
    "port_empty_prep": "港口整备中空箱(未入可用)",
    "transit_loaded": "在途去程重箱", "transit_empty": "反空在途空箱",
    "xtz_loaded": "新台子重箱", "xtz_empty": "新台子空箱",
    "line330_loaded": "330线上重箱", "line330_empty": "330线上空箱",
    "ground330_loaded": "330落地重箱", "ground330_empty": "330落地空箱",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    cols = ",\n  ".join(f"{f} INTEGER NOT NULL DEFAULT 0" for f in NODE_FIELDS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS container_pool_snapshot (
          id TEXT PRIMARY KEY,
          snapshot_date TEXT NOT NULL,
          project TEXT NOT NULL,
          ship_name TEXT NOT NULL,
          {cols},
          total_loaded INTEGER NOT NULL DEFAULT 0,
          total_empty INTEGER NOT NULL DEFAULT 0,
          total_pool INTEGER NOT NULL DEFAULT 0,
          inferred_fields TEXT DEFAULT '',
          source TEXT DEFAULT 'broadcast',
          note TEXT DEFAULT '',
          created_at TEXT, updated_at TEXT,
          UNIQUE(project, ship_name, snapshot_date)
        )
    """)
    conn.commit()


def record(conn: sqlite3.Connection, *, snapshot_date: str, project: str,
           ship_name: str, nodes: dict, inferred: str, source: str,
           note: str, now: str) -> dict:
    vals = {f: int(nodes.get(f) or 0) for f in NODE_FIELDS}
    total_loaded = sum(vals[f] for f in LOADED_FIELDS)
    total_empty = sum(vals[f] for f in EMPTY_FIELDS)
    total_pool = total_loaded + total_empty
    rid = hashlib.sha1(f"{project}|{ship_name}|{snapshot_date}".encode()).hexdigest()
    cols = (["id", "snapshot_date", "project", "ship_name"] + NODE_FIELDS +
            ["total_loaded", "total_empty", "total_pool",
             "inferred_fields", "source", "note", "created_at", "updated_at"])
    row = ([rid, snapshot_date, project, ship_name] + [vals[f] for f in NODE_FIELDS] +
           [total_loaded, total_empty, total_pool, inferred, source, note, now, now])
    conn.execute(
        f"INSERT INTO container_pool_snapshot ({','.join(cols)}) "
        f"VALUES ({','.join('?'*len(cols))}) "
        f"ON CONFLICT(project, ship_name, snapshot_date) DO UPDATE SET " +
        ",".join(f"{c}=excluded.{c}" for c in cols if c not in
                 ("id", "snapshot_date", "project", "ship_name", "created_at")),
        row,
    )
    return {"id": rid, "total_loaded": total_loaded,
            "total_empty": total_empty, "total_pool": total_pool, **vals}


CONTAINER_BATCH = {("jiusan", "和谐1"): "e96f4b3b83c74b4891c6b0957f6989bb827de45b"}


def check(conn: sqlite3.Connection, project: str, ship: str, date: str) -> list[str]:
    """每日盘点守恒校验 —— 不符点列出来给人工核实,不自动改数。

    校验项:
      1. 任一节点箱数为负(发端到端为负)
      2. 在途去程重箱 vs 95306 已发车货票箱数(明显不符 → 发运记录对不上)
      3. 330落地重 + 新台子重 不超过 95306 已到/已交付箱数(凭空多重箱)
      4. 昨日→今日 池总变化:正常守恒(Δ=0),只有投新箱才该增 → 否则报
    """
    row = conn.execute(
        "SELECT * FROM container_pool_snapshot WHERE project=? AND ship_name=? "
        "AND snapshot_date=?", (project, ship, date)).fetchone()
    if not row:
        return [f"✗ {date} 无快照,先 --record"]
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM container_pool_snapshot LIMIT 0").description]
    r = dict(zip(cols, row))
    issues: list[str] = []

    # 1. 负数
    for f in NODE_FIELDS:
        if r[f] < 0:
            issues.append(f"✗ 节点为负:{FIELD_LABELS[f]} = {r[f]}")

    # 2/3. 对 95306 发运记录
    bid = CONTAINER_BATCH.get((project, ship))
    if bid:
        st = {k: v for k, v in conn.execute(
            "SELECT status_name, count(*) FROM wagon_container_shipments "
            "WHERE batch_id=? GROUP BY status_name", (bid,)).fetchall()}
        departed = st.get("已发车", 0)
        arrived_delivered = st.get("已到达", 0) + st.get("货物已交付", 0)
        if abs(r["transit_loaded"] - departed) > 4:   # >2车容差
            issues.append(
                f"⚠ 在途去程重箱={r['transit_loaded']} vs 95306 已发车={departed}"
                f"(差 {r['transit_loaded']-departed:+d},发运记录对不上)")
        if r["ground330_loaded"] + r["xtz_loaded"] > arrived_delivered + 4:
            issues.append(
                f"⚠ 330落地重+新台子重={r['ground330_loaded']+r['xtz_loaded']} "
                f"> 95306 已到/已交付={arrived_delivered}(凭空多重箱)")

    # 4. 昨日→今日 守恒
    prev = conn.execute(
        "SELECT total_pool, ground330_empty, port_empty FROM container_pool_snapshot "
        "WHERE project=? AND ship_name=? AND snapshot_date<? "
        "ORDER BY snapshot_date DESC LIMIT 1", (project, ship, date)).fetchone()
    if prev:
        d_pool = r["total_pool"] - prev[0]
        if d_pool != 0:
            note_has_new = any(k in (r["note"] or "") for k in ("新箱", "投箱", "新增箱"))
            flag = "" if (d_pool > 0 and note_has_new) else "  ← 未注明投新箱,核实"
            issues.append(f"⚠ 池总 Δ={d_pool:+d}(昨 {prev[0]} → 今 {r['total_pool']}){flag}")

    return issues


def show(conn: sqlite3.Connection, project: str, ship: str, limit: int = 10) -> None:
    rows = conn.execute(
        "SELECT * FROM container_pool_snapshot WHERE project=? AND ship_name=? "
        "ORDER BY snapshot_date DESC LIMIT ?", (project, ship, limit),
    ).fetchall()
    if not rows:
        print("(无快照)")
        return
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM container_pool_snapshot LIMIT 0").description]
    rows = [dict(zip(cols, r)) for r in rows][::-1]
    print(f"集装箱池快照 — {project} / {ship}")
    print(f"{'日期':<12}{'重箱':>6}{'空箱':>6}{'池总':>6}   节点(重|空)")
    prev = None
    for r in rows:
        delta = f"  Δ池 {r['total_pool']-prev:+d}" if prev is not None else ""
        prev = r["total_pool"]
        loaded = " ".join(f"{FIELD_LABELS[f].replace('重箱','').replace('空箱','')}{r[f]}"
                          for f in LOADED_FIELDS if r[f])
        empty = " ".join(f"{FIELD_LABELS[f].replace('空箱','').replace('重箱','')}{r[f]}"
                         for f in EMPTY_FIELDS if r[f])
        print(f"{r['snapshot_date']:<12}{r['total_loaded']:>6}{r['total_empty']:>6}"
              f"{r['total_pool']:>6}{delta}")
        print(f"             重[{loaded}]  空[{empty}]")
        if r["inferred_fields"]:
            print(f"             ⚠ 推断:{r['inferred_fields']}  ({r['source']}) {r['note']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--show", action="store_true")
    p.add_argument("--check", metavar="DATE", help="对该日快照跑守恒校验")
    p.add_argument("--record", metavar="DATE")
    p.add_argument("--project", default="jiusan")
    p.add_argument("--ship", default="和谐1")
    for f in NODE_FIELDS:
        p.add_argument(f"--{f.replace('_', '-')}", type=int, default=0)
    p.add_argument("--inferred", default="")
    p.add_argument("--source", default="field")
    p.add_argument("--note", default="")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(str(SOP_DB))
    ensure_schema(conn)
    try:
        if args.record:
            nodes = {f: getattr(args, f) for f in NODE_FIELDS}
            res = record(conn, snapshot_date=args.record, project=args.project,
                         ship_name=args.ship, nodes=nodes, inferred=args.inferred,
                         source=args.source, note=args.note, now=now_iso_beijing())
            if args.apply:
                conn.commit(); tag = "COMMIT ✓"
            else:
                conn.rollback(); tag = "DRY-RUN(加 --apply 提交)"
            print(f"{tag} {args.record} {args.ship}: "
                  f"重 {res['total_loaded']} + 空 {res['total_empty']} = "
                  f"池 {res['total_pool']} 箱")
        if args.check:
            issues = check(conn, args.project, args.ship, args.check)
            if issues:
                print(f"盘点校验 {args.check} — {len(issues)} 处不符,请人工核实:")
                for i in issues:
                    print(f"  {i}")
            else:
                print(f"盘点校验 {args.check} ✓ 全部守恒,无不符点")
        if args.show or not (args.record or args.check):
            show(conn, args.project, args.ship)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
