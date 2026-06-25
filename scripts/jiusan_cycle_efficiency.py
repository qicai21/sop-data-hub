"""九三集装箱循环列效率统计 + 到厂时间推算(#issue-20260623 问题3)。

从 wagon_container_shipments 各状态时间戳(港发车 departed→新台子到达 arrived→
三三0卸货交付 delivered),按 home_cycle_no(车体池)分列、按发车日分趟,算各段时长:
  - 在途(港→新台子) = arrived - departed
  - 卸货 dwell(新台子停留) = delivered - arrived
  - 周转(返程+港装) = 下趟departed - 本趟delivered
  - 整圈 = 下趟departed - 本趟departed
给各列/整体均值 + 各列当前位置 + 下一节点 ETA(用历史均值推),其中"在途时间"
用于到厂(新台子到达)预测。循环车号复用 → 按 departed_at 趟次(非car_no)分趟。

用法:python scripts/jiusan_cycle_efficiency.py [--ship 和谐1]
"""
from __future__ import annotations
import argparse, sqlite3, sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"


def _dt(s):
    if not s:
        return None
    s = str(s)[:19].replace("T", " ")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _h(a, b):
    """b-a 小时(float),缺一返 None。"""
    if a and b:
        return round((b - a).total_seconds() / 3600, 1)
    return None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def fetch_trips(conn, batch_id):
    """→ {cyc: [ {trip, dep, arr, deliv, boxes} ... 按 dep 升序 ]}"""
    rows = conn.execute("""
        WITH t AS (
          SELECT wbp.home_cycle_no cyc, substr(wcs.departed_at,1,10) trip,
                 min(wcs.departed_at) dep, min(wcs.arrived_at) arr,
                 min(NULLIF(wcs.delivered_at,'')) deliv, count(DISTINCT wcs.box_no) boxes
          FROM wagon_container_shipments wcs
          JOIN wagon_body_pool wbp ON wcs.car_no=wbp.car_no AND wbp.project='jiusan'
          WHERE wcs.batch_id=? AND wcs.departed_at!='' GROUP BY cyc, trip)
        SELECT cyc, trip, dep, arr, deliv, boxes FROM t ORDER BY cyc, dep""", (batch_id,)).fetchall()
    out: dict = {}
    for cyc, trip, dep, arr, deliv, boxes in rows:
        out.setdefault(cyc, []).append(
            {"trip": trip, "dep": _dt(dep), "arr": _dt(arr), "deliv": _dt(deliv), "boxes": boxes})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ship", default="和谐1")
    a = ap.parse_args()
    conn = sqlite3.connect(str(SOP_DB))
    bid = conn.execute(
        "SELECT id FROM release_batches WHERE project='jiusan' AND ship_name=? AND batch_sequence='lot01'",
        (a.ship,)).fetchone()
    if not bid:
        print(f"无 {a.ship} lot01 batch"); return
    trips = fetch_trips(conn, bid[0])
    now = _dt(now_iso_beijing())

    all_transit, all_dwell, all_turn, all_cycle = [], [], [], []
    print(f"=== {a.ship} 循环列效率(各段时长,单位小时)===")
    for cyc in sorted(trips):
        ts = trips[cyc]
        seg_t, seg_d, seg_turn, seg_cyc = [], [], [], []
        for i, t in enumerate(ts):
            transit = _h(t["dep"], t["arr"]); dwell = _h(t["arr"], t["deliv"])
            seg_t.append(transit); seg_d.append(dwell)
            if i + 1 < len(ts):
                turn = _h(t["deliv"], ts[i + 1]["dep"]); cyc_h = _h(t["dep"], ts[i + 1]["dep"])
                seg_turn.append(turn); seg_cyc.append(cyc_h)
        all_transit += seg_t; all_dwell += seg_d; all_turn += seg_turn; all_cycle += seg_cyc
        print(f"  {a.ship} {cyc}号列({len(ts)}趟): 在途均{_avg(seg_t)}h 卸货dwell均{_avg(seg_d)}h "
              f"周转均{_avg(seg_turn)}h 整圈均{_avg(seg_cyc)}h")
    print(f"  ── 整体均值: 在途 {_avg(all_transit)}h | 卸货 {_avg(all_dwell)}h | "
          f"周转 {_avg(all_turn)}h | 整圈 {_avg(all_cycle)}h ──")

    avg_transit, avg_dwell, avg_turn = _avg(all_transit), _avg(all_dwell), _avg(all_turn)
    print(f"\n=== 各列当前位置 + 下一节点 ETA(历史均值推算)===")
    from datetime import timedelta
    for cyc in sorted(trips):
        last = trips[cyc][-1]
        if last["deliv"]:
            # 已交付 → 返程/在港装,ETA 下趟港发车
            eta = last["deliv"] + timedelta(hours=avg_turn) if avg_turn else None
            print(f"  {cyc}号列: 末趟已卸交付({last['deliv'].strftime('%m-%d %H:%M')})→ 返程/在港装"
                  f"  预计下趟港发车 ~{eta.strftime('%m-%d %H:%M') if eta else '?'}")
        elif last["arr"]:
            eta = last["arr"] + timedelta(hours=avg_dwell) if avg_dwell else None
            print(f"  {cyc}号列: 在新台子卸货(到{last['arr'].strftime('%m-%d %H:%M')})"
                  f"  预计交付完成 ~{eta.strftime('%m-%d %H:%M') if eta else '?'}")
        elif last["dep"]:
            eta = last["dep"] + timedelta(hours=avg_transit) if avg_transit else None
            print(f"  {cyc}号列: 在途去程(发{last['dep'].strftime('%m-%d %H:%M')})"
                  f"  预计到厂(新台子) ~{eta.strftime('%m-%d %H:%M') if eta else '?'} ← 到厂预测")
    conn.close()


if __name__ == "__main__":
    main()
