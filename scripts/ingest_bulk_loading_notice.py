"""薛雪红「发运报表」xlsx → 散粮简装车通知单台账(bulk_loading_notice_wagon)。

#issue-20260620 item4:把单趟一次性 seed 泛化成可复用 ingest。九三大豆发运群
[GROUP093] 成员「铁晟 薛雪红」每日发 `<船名> 散粮车<日期>.xlsx`,内含该船逐日逐车
清单。本脚本读它落台账,供 sync_jiusan_bulk_wagons 按 ydid 分船路由(诚信/和谐1…)。

⚠️ 95306 数据权威最高;本表只提供**分票(哪车属哪船)**。ydid 按 car_no+日期 从
wagon_shipments(已含 95306 同步车)反查锁定(散粮循环车号会复用,必按当日锁这趟)。

xlsx 约定:每个 sheet 标题=月.日(如 6.21→当年-06-21);列= 序号|车型|车号|净重|标重|出库地点。
船名默认取文件名首个空白分隔 token(`诚信 散粮车6.21(1).xlsx` → 诚信),可 --ship 覆盖。
track 字段对报表来源填**船名**(报表按船不按股道;与人工通知单的真实股道如「七道」不撞)。

用法:python scripts/ingest_bulk_loading_notice.py <xlsx> [--ship 诚信] [--year 2026]
                                                  [--project jiusan] [--lot lot02] [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

SOP_DB = REPO / "data" / "sop_agent.db"
DESTINATION = "新台子"
CONSIGNEE = "锦州新铁晟(代)"


def _ship_from_filename(path: Path) -> str:
    # "诚信 散粮车6.21(1).xlsx" → "诚信"
    return re.split(r"\s+", path.stem.strip())[0]


def _sheet_date(title: str, year: int) -> str | None:
    m = re.match(r"\s*(\d{1,2})[.\-/](\d{1,2})", title.strip())
    if not m:
        return None
    return f"{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def parse_xlsx(path: Path, year: int) -> dict[str, list[tuple]]:
    """Return {date: [(seq, car_model, car_no), ...]}."""
    wb = openpyxl.load_workbook(str(path), data_only=True)
    out: dict[str, list[tuple]] = {}
    for ws in wb.worksheets:
        d = _sheet_date(ws.title, year)
        if not d:
            continue
        rows = []
        for r in ws.iter_rows(values_only=True):
            if r and isinstance(r[0], int) and len(r) > 2 and r[2]:
                seq = int(r[0])
                model = str(r[1]).strip() if len(r) > 1 and r[1] else ""
                car_no = str(r[2]).strip()
                rows.append((seq, model, car_no))
        if rows:
            out[d] = rows
    return out


def _ydid_for(conn: sqlite3.Connection, car_no: str, date: str) -> tuple[str | None, int]:
    """反查该车当日货票 ydid(从 wagon_shipments,已含 95306 同步)。返回 (ydid, 命中数)。"""
    yds = [r[0] for r in conn.execute(
        "SELECT DISTINCT ydid FROM wagon_shipments "
        "WHERE car_no=? AND substr(ticketed_at,1,10)=?",
        (car_no, date),
    ).fetchall()]
    return (yds[0] if len(yds) == 1 else None), len(yds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--ship", default="")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--project", default="jiusan")
    ap.add_argument("--lot", default="lot02")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    path = Path(a.xlsx)
    if not path.exists():
        print(f"⚠ 文件不存在: {path}")
        return
    ship = a.ship or _ship_from_filename(path)
    by_date = parse_xlsx(path, a.year)
    if not by_date:
        print("⚠ 未解析出任何日期 sheet")
        return

    now = now_iso_beijing()
    src = f"九三发运群[GROUP093]/薛雪红报表:{path.name}"
    conn = sqlite3.connect(str(SOP_DB))
    total_ins = total_unmatched = total_multi = 0
    print(f"船={ship}  项目={a.project}  lot={a.lot}  文件={path.name}")
    for date in sorted(by_date):
        rows = by_date[date]
        # 报表为该(项目+船+日期)分票的权威源:先清旧条目(七道/晨报/旧报表等任何
        # track),再按报表插入,避免重复累积、保证幂等。其它船的条目不动。
        conn.execute(
            "DELETE FROM bulk_loading_notice_wagon "
            "WHERE project=? AND ship_name=? AND notice_date=?",
            (a.project, ship, date),
        )
        ins = unmatched = multi = 0
        miss = []
        for seq, model, car_no in rows:
            ydid, n = _ydid_for(conn, car_no, date)
            if n == 0:
                unmatched += 1; miss.append(car_no)
            elif n > 1:
                multi += 1
            rid = hashlib.sha1(f"{a.project}|{date}|{ship}|{seq}".encode()).hexdigest()[:24]
            conn.execute(
                "INSERT OR REPLACE INTO bulk_loading_notice_wagon "
                "(id,project,notice_date,track,total_cars,car_seq,car_no,car_model,"
                " ship_name,lot,ydid,destination,consignee,source_ref,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, a.project, date, ship, len(rows), seq, car_no, model,
                 ship, a.lot, ydid, DESTINATION, CONSIGNEE, src, now),
            )
            ins += 1
        total_ins += ins; total_unmatched += unmatched; total_multi += multi
        print(f"  {date}: {len(rows)}车 → 落台账{ins} (ydid未匹配{unmatched}"
              f"{('/多义'+str(multi)) if multi else ''})"
              f"{('  未匹配:'+str(miss[:6])) if miss else ''}")
    if a.apply:
        conn.commit()
        print(f"\nCOMMIT ✓ 共落 {total_ins} 行 (未匹配ydid {total_unmatched}, 多义 {total_multi})")
        print("  下一步:跑 sync_jiusan_bulk_wagons.py --apply 按台账重路由分船")
    else:
        conn.rollback()
        print(f"\nDRY-RUN(加 --apply 提交)共 {total_ins} 行")
    conn.close()


if __name__ == "__main__":
    main()
