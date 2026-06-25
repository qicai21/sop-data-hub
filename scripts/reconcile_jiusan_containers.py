"""九三集装箱按船对账(可重复跑,幂等)。

工作流:每条船发完(或阶段性),港方给一份「<船> 货票」xlsx(新台子 sheet:车号+货票号+2箱号)。
本脚本据此把箱归到正确的船:
  1. 建/补 container_loading_notice 台账(box 级:ydid+box_no → ship)。
  2. 文件里的箱 → --ship;--other-ship 收口"同期在系统里、但不在本文件"的箱(2船并行期)。
  3. --exception 处理"同一货票里个别箱归对船"(如一车拉两船)。
  4. 按台账重分 wagon_container_shipments.batch_id → 各船 lot01,重算 shipped_weight。

集装箱拆船唯一事实源 = 港方货票清单(95306 货票/到站/收货人全相同,字段拆不开)。
货票号(GZD…)藏在 95306 raw_core_json,经 ydid 桥接到系统 box。

用法:
  python scripts/reconcile_jiusan_containers.py --file <xlsx> --ship 和谐1 --other-ship 诚信 \
      --exception GZDJW0496546:1512630:诚信        # 可多个;格式 货票号:箱尾号:归属船
  加 --apply 才写库(默认干跑预览)。
"""
import argparse, hashlib, json, re, sqlite3, sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sop_hub.utils.time import now_iso_beijing_compact  # noqa: E402

HUB = "data/sop_agent.db"
RAIL = "/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3"
PROJECT = "jiusan"
_DEST_LIKE = "(destination_name LIKE '%新台子%' OR destination_name LIKE '%三三0%' OR destination_name LIKE '%三三零%')"


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_loading_notice (
            id TEXT PRIMARY KEY,        -- sha1(ydid|box_no) 船无关,重路由不换 id
            project TEXT, ship_name TEXT, ydid TEXT, box_no TEXT,
            hph TEXT, car_no TEXT, source_ref TEXT, created_at TEXT
        )""")
    conn.commit()


def load_ydid2hph(rail):
    m = {}
    for ydid, raw in rail.execute(
        f"SELECT ydid, raw_core_json FROM shipments WHERE {_DEST_LIKE} AND ticketed_at LIKE '2026-%'"):
        if raw:
            g = re.findall(r"GZD[A-Z]{2}[0-9]{6,}", raw)
            if g:
                m[ydid] = g[0]
    return m


def read_file_hph(xlsx):
    ws = openpyxl.load_workbook(xlsx, data_only=True)["新台子"]
    hph = set()
    for r in ws.iter_rows(min_row=4, values_only=True):
        if r[2] and str(r[2]).strip() not in ("合计", "") and r[3]:
            hph.add(str(r[3]).strip())
    return hph


def ship_lot01(conn, ship):
    r = conn.execute(
        "SELECT id FROM release_batches WHERE project=? AND ship_name=? AND batch_sequence='lot01'",
        (PROJECT, ship)).fetchone()
    return r[0] if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--ship", required=True, help="本文件所属船(如 和谐1)")
    ap.add_argument("--other-ship", help="收口同期不在本文件的箱(2船并行期,如 诚信)")
    ap.add_argument("--exception", action="append", default=[],
                    help="货票号:箱尾号:归属船(可多次)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    exc = {}  # (hph, box_suffix) -> ship
    for e in a.exception:
        hph, suf, shp = e.split(":")
        exc[(hph, suf)] = shp

    rail = sqlite3.connect(RAIL)
    hub = sqlite3.connect(HUB)
    ensure_table(hub)

    ydid2hph = load_ydid2hph(rail)
    file_hph = read_file_hph(a.file)
    own_lot = ship_lot01(hub, a.ship)
    other_lot = ship_lot01(hub, a.other_ship) if a.other_ship else None
    print(f"文件货票 {len(file_hph)} 个 | {a.ship} lot01={own_lot} | {a.other_ship} lot01={other_lot}")
    if not own_lot or (a.other_ship and not other_lot):
        print("!! lot01 batch 未找到,退出"); return

    # 取本船 + other 船 lot01 现有箱(同期总集),逐箱定船
    bids = [own_lot] + ([other_lot] if other_lot else [])
    ph = ",".join("?" * len(bids))
    boxes = hub.execute(
        f"SELECT id, car_no, box_no, ydid, batch_id FROM wagon_container_shipments WHERE batch_id IN ({ph})",
        bids).fetchall()

    now = now_iso_beijing_compact()
    plan, ledger = [], []  # plan: (box_id, target_lot, ship); ledger: 台账行
    for bid, car, box, ydid, cur in boxes:
        hph = ydid2hph.get(ydid)
        # 默认:命中本文件 → 本船;否则 → other 船
        ship = a.ship if hph in file_hph else (a.other_ship or a.ship)
        # 例外覆盖(同票个别箱)
        for (ehph, esuf), eship in exc.items():
            if hph == ehph and str(box).endswith(esuf):
                ship = eship
        target = own_lot if ship == a.ship else other_lot
        if target and cur != target:
            plan.append((bid, target, ship))
        ledger.append((hashlib.sha1(f"{ydid}|{box}".encode()).hexdigest(),
                       PROJECT, ship, ydid, box, hph or "", car, Path(a.file).name, now))

    from collections import Counter
    cnt = Counter(s for *_, s in [(p[2],) and (None, None, p[2]) for p in plan])
    print(f"需重路由箱: {len(plan)}  →船分布: {dict(Counter(p[2] for p in plan))}")
    print(f"台账行(box级): {len(ledger)}")

    if not a.apply:
        print("\n[干跑] 加 --apply 才写库。样本重路由:")
        for p in plan[:5]:
            print("  ", p)
        return

    # 写台账(幂等 REPLACE)
    hub.executemany(
        "INSERT OR REPLACE INTO container_loading_notice "
        "(id,project,ship_name,ydid,box_no,hph,car_no,source_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ledger)
    # 重路由 box.batch_id + ship_name
    for bid, target, ship in plan:
        hub.execute("UPDATE wagon_container_shipments SET batch_id=?, ship_name=?, updated_at=? WHERE id=?",
                    (target, ship, now, bid))
    hub.commit()
    print(f"已写台账 {len(ledger)} 行,重路由 {len(plan)} 箱。")

    from sop_hub.sop.shipped_weight import compute_for_release_batch
    for lot, nm in [(own_lot, a.ship)] + ([(other_lot, a.other_ship)] if other_lot else []):
        sw = compute_for_release_batch(lot, db_path=HUB)
        n = hub.execute("SELECT count(*) FROM wagon_container_shipments WHERE batch_id=?", (lot,)).fetchone()[0]
        print(f"  {nm} lot01: {n}箱 {sw.get('shipped_weight_tons')}吨")


if __name__ == "__main__":
    main()
