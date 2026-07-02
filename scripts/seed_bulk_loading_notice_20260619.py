"""建散粮「简装车通知单」台账 + 落 2026-06-19 七道 50节 这趟的车号→船拆分。

为什么要它:诚信、和谐1 两船散粮**到站相同(新台子)**,95306 货票拆不开谁是谁;
唯一事实源是港方装车时出的「简装车通知单」(每趟一张,标了"诚信30节/和谐1 20节")。
sync_jiusan_bulk_wagons 现写死全挂和谐1 → 诚信散粮混入和谐1 lot02、超发 -2072t。
本表把每趟的 car_no→ship 映射落库,供 sync 改造后 JOIN 拆船(见同日 issue 工单)。

数据源:数据单发群 wx_228 简装车通知单(2026-06-19 七道 50节)。
拆分(按通知单标注位置):序号1-30=诚信(30节)、序号31-50=和谐1(20节)。
序号2 经 wagon_shipments 反查确认为 8101357(OCR 误读 8011357,库中无此车 6/19 记录)。
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

NOTICE_DATE = "2026-06-19"
TRACK = "七道"
DESTINATION = "新台子"
CONSIGNEE = "锦州新铁晟(代)"           # 通知单收货人/代理(新台子大豆)
SOURCE_REF = "数据单发群/wx_228 简装车通知单"

# (序号, 车型, 车号, 船名)  —— 序号1-30 诚信 / 31-50 和谐1
ROWS = [
    (1, "L70", "8107032", "诚信"), (2, "L18", "8101357", "诚信"),
    (3, "L18", "8100992", "诚信"), (4, "L18", "8101089", "诚信"),
    (5, "L70", "8105486", "诚信"), (6, "L18", "8100114", "诚信"),
    (7, "L18", "8102537", "诚信"), (8, "L18", "8100903", "诚信"),
    (9, "L18", "8100906", "诚信"), (10, "L70", "8107864", "诚信"),
    (11, "L18", "8101043", "诚信"), (12, "L18", "8101423", "诚信"),
    (13, "L18", "8101776", "诚信"), (14, "L18", "8101524", "诚信"),
    (15, "L18", "8100090", "诚信"), (16, "L18", "8100207", "诚信"),
    (17, "L18", "8100751", "诚信"), (18, "L70", "8107772", "诚信"),
    (19, "L70", "8107303", "诚信"), (20, "L18", "8100262", "诚信"),
    (21, "L18", "8101977", "诚信"), (22, "L18", "8100809", "诚信"),
    (23, "L18", "8100269", "诚信"), (24, "L18", "8100237", "诚信"),
    (25, "L18", "8101485", "诚信"), (26, "L18", "8101759", "诚信"),
    (27, "L18", "8101840", "诚信"), (28, "L18", "8100958", "诚信"),
    (29, "L18", "8101584", "诚信"), (30, "L70", "8107523", "诚信"),
    (31, "L18", "8100204", "和谐1"), (32, "L18", "8100211", "和谐1"),
    (33, "L18", "8101339", "和谐1"), (34, "L70", "8107541", "和谐1"),
    (35, "L18", "8101209", "和谐1"), (36, "L18", "8101030", "和谐1"),
    (37, "L70", "8107535", "和谐1"), (38, "L18", "8105411", "和谐1"),
    (39, "L18", "8100705", "和谐1"), (40, "L70", "8105451", "和谐1"),
    (41, "L18", "8101653", "和谐1"), (42, "L18", "8101060", "和谐1"),
    (43, "L18", "8100677", "和谐1"), (44, "L18", "8100987", "和谐1"),
    (45, "L18", "8102008", "和谐1"), (46, "L70", "8105777", "和谐1"),
    (47, "L18", "8100563", "和谐1"), (48, "L18", "8100019", "和谐1"),
    (49, "L18", "8100743", "和谐1"), (50, "L18", "8100197", "和谐1"),
]

DDL = """
CREATE TABLE IF NOT EXISTS bulk_loading_notice_wagon (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  notice_date TEXT NOT NULL,
  track TEXT,
  total_cars INTEGER,
  car_seq INTEGER,
  car_no TEXT NOT NULL,
  car_model TEXT,
  ship_name TEXT NOT NULL,
  lot TEXT DEFAULT 'lot02',
  ydid TEXT,                      -- 该车本趟 95306 货票号(car_no+notice_date 反查)
  destination TEXT,
  consignee TEXT,
  source_ref TEXT,
  created_at TEXT,
  UNIQUE(project, notice_date, track, car_seq, ship_name)
)
"""


def main(apply: bool = False):
    now = now_iso_beijing()
    conn = sqlite3.connect(str(SOP_DB))
    conn.execute(DDL)
    cur = conn.cursor()
    cnt = {"诚信": 0, "和谐1": 0}
    ydid_warn = []
    for seq, model, car_no, ship in ROWS:
        # 反查该车 notice_date 当日货票 ydid(散粮循环车号会复用,按日锁这趟)
        yds = [r[0] for r in conn.execute(
            "SELECT DISTINCT ydid FROM wagon_shipments "
            "WHERE car_no=? AND substr(ticketed_at,1,10)=?",
            (car_no, NOTICE_DATE)).fetchall()]
        ydid = yds[0] if len(yds) == 1 else None
        if len(yds) != 1:
            ydid_warn.append((seq, car_no, len(yds)))
        rid = hashlib.sha1(f"jiusan|{NOTICE_DATE}|{TRACK}|{seq}".encode()).hexdigest()
        cur.execute(
            "INSERT OR REPLACE INTO bulk_loading_notice_wagon "
            "(id,project,notice_date,track,total_cars,car_seq,car_no,car_model,"
            " ship_name,lot,ydid,destination,consignee,source_ref,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, "jiusan", NOTICE_DATE, TRACK, len(ROWS), seq, car_no, model,
             ship, "lot02", ydid, DESTINATION, CONSIGNEE, SOURCE_REF, now))
        cnt[ship] += 1

    print(f"装车通知单 {NOTICE_DATE} {TRACK} {len(ROWS)}节  →  "
          f"诚信 {cnt['诚信']} / 和谐1 {cnt['和谐1']}")
    matched = sum(1 for s, m, c, sh in ROWS
                  if conn.execute("SELECT 1 FROM wagon_shipments WHERE car_no=? "
                                  "AND substr(ticketed_at,1,10)=?",
                                  (c, NOTICE_DATE)).fetchone())
    print(f"对 wagon_shipments 6/19 命中:{matched}/{len(ROWS)}")
    if ydid_warn:
        print(f"⚠ ydid 未唯一(零或多)车:{ydid_warn}")
    else:
        print("ydid 全部唯一锁定 ✓")
    if apply:
        conn.commit(); print("COMMIT ✓")
    else:
        conn.rollback(); print("DRY-RUN(加 --apply 提交)")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    main(apply=a.apply)
