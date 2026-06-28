"""九三大豆货票自动对账 —— 第一阶段:审查(只读)。

自动扫 WeChat `msg/file/<月>/` → 各船**最新版**『大豆 X 货票.xlsx』(含 `新台子`=集装箱、
`散粮车`=散粮 两个 sheet)→ 按货票号(GZD)建"应船"权威 → 逐条比对 DB 现挂船 → 出
**三类不符**报告。纯只读、可反复跑。

三类:
  ① 集装箱/散粮 **应≠实**(错挂别船,如诚信货票堆和谐1)；
  ② **无权威覆盖**:箱/车现挂某条「有文件」的船,但该货票号不在任何文件里
     —— 多半是比文件更新的新货(如 6-28 新箱),或来路不明被 DEFAULT_SHIP 堆过来的;
  ③ **DB hph 列为空**:sync 没把货票号写进库(靠 ydid→rail 桥接补判),数据质量问题。
没有文件的船(玛格丽特/昆娜等历史船)→ 不报,避免误判。

复用 reconcile_jiusan_containers 的 RAIL/_DEST_LIKE/版本选择,口径一致。
"""
import argparse
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import reconcile_jiusan_containers as rec  # noqa: E402

HUB = "data/sop_agent.db"
PROJECT = "jiusan"
KNOWN_BOX_EXCEPTIONS = {"GZDJW0496546"}  # 一票两船,box 级人工拆,不算不符


def _wx_file_dirs(month):
    base = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    pat = f"*/msg/file/{month}" if month else "*/msg/file/2026-*"
    return sorted(base.glob(pat), reverse=True)


def _parse_ship_version(fname):
    m = re.search(r"大豆\s*(\S+?)\s*货票", fname)
    if not m:
        return None, 0
    v = re.search(r"\((\d+)\)", fname)
    return m.group(1).strip(), (int(v.group(1)) if v else 0)


def find_latest_tickets(month):
    """ship -> (Path, version);跨月取版本号最大、同版本取 mtime 最新。"""
    best = {}
    for d in _wx_file_dirs(month):
        for f in d.glob("*大豆*货票*.xlsx"):
            ship, ver = _parse_ship_version(f.name)
            if not ship:
                continue
            key = (ver, f.stat().st_mtime)
            if ship not in best or key > best[ship][1]:
                best[ship] = (f, key)
    return {s: (p, k[0]) for s, (p, k) in best.items()}


def read_sheet_hph(path, sheet):
    """读某 sheet 的货票号集合(新台子/散粮车通用,货票号在第 4 列)。"""
    try:
        ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    except KeyError:
        return set()
    out = set()
    for r in ws.iter_rows(min_row=4, values_only=True):
        h = r[3] if len(r) > 3 else None
        if h and str(h).strip().startswith("GZD"):
            out.add(str(h).strip())
    return out


def load_ydid_info(rail):
    """ydid -> (货票号, 日期)。新台子到站、2026(集装箱+散粮同到站)。"""
    m = {}
    for ydid, raw, tk in rail.execute(
        f"SELECT ydid, raw_core_json, ticketed_at FROM shipments "
        f"WHERE {rec._DEST_LIKE} AND ticketed_at LIKE '2026-%'"):
        if raw:
            g = re.findall(r"GZD[A-Z]{2}[0-9]{6,}", raw)
            if g:
                m[ydid] = (g[0], str(tk)[:10])
    return m


def audit_one(hub, table, hph2ship, ydid2hph, ydid2date, filed, unit):
    rows = hub.execute(
        f"SELECT t.ydid, t.hph, rb.ship_name FROM {table} t "
        f"JOIN release_batches rb ON t.batch_id=rb.id WHERE rb.project=?", (PROJECT,)).fetchall()
    mism_hph = defaultdict(set)
    mism_n = Counter()
    no_auth = []       # (实船, 日期) — 仅"有文件的船"
    no_hph_col = 0     # DB hph 列空
    skip_nofile = 0    # 现挂船没文件,跳过不判
    exc = ok = 0
    for ydid, dbhph, cur in rows:
        if not (dbhph or "").strip():
            no_hph_col += 1
        hph = (dbhph or "").strip() or ydid2hph.get(ydid)
        if hph in KNOWN_BOX_EXCEPTIONS:
            exc += 1; continue
        ships = hph2ship.get(hph)
        if not ships:
            if cur in filed:                       # 在"有文件的船"上却查无此货票 → 可疑
                no_auth.append((cur, ydid2date.get(ydid, "?")))
            else:
                skip_nofile += 1
            continue
        if len(ships) > 1:
            continue
        should = next(iter(ships))
        if should != cur:
            mism_hph[(should, cur)].add(hph); mism_n[(should, cur)] += 1
        else:
            ok += 1

    if mism_n:
        for (s, c), n in sorted(mism_n.items(), key=lambda x: -x[1]):
            print(f"  ✗ 不符 应【{s}】/ 实【{c}】: {n}{unit} / {len(mism_hph[(s, c)])}货票")
    else:
        print(f"  ✓ 无『应≠实』不符")
    if no_auth:
        bd = Counter(no_auth)
        tot = len(no_auth)
        print(f"  ⚠️ 无权威覆盖 {tot}{unit}(现挂有文件的船、但货票号不在任何文件 → 疑似新货/错堆):")
        for (s, d), n in sorted(bd.items(), key=lambda x: (x[0][0], x[0][1])):
            print(f"       实挂【{s}】 {d}: {n}{unit}")
    if no_hph_col:
        print(f"  ⚠️ DB {table}.hph 列为空: {no_hph_col}{unit}(sync 没存货票号,本审计靠 ydid→rail 桥接补判)")
    print(f"  小结: 一致{ok} | 不符{sum(mism_n.values())} | 无权威{len(no_auth)} | "
          f"无文件船跳过{skip_nofile} | 例外{exc}")


def audit(month):
    latest = find_latest_tickets(month)
    if not latest:
        print("未在 WeChat 文件夹找到任何『大豆…货票』xlsx。"); return
    rail = sqlite3.connect(rec.RAIL)
    hub = sqlite3.connect(HUB)
    info = load_ydid_info(rail)
    ydid2hph = {y: h for y, (h, _) in info.items()}
    ydid2date = {y: d for y, (_, d) in info.items()}

    cont, bulk = defaultdict(set), defaultdict(set)
    print("=== 权威文件(各船最新版,新台子=集装箱 / 散粮车=散粮)===")
    for ship, (path, ver) in sorted(latest.items()):
        ch = read_sheet_hph(path, "新台子")
        bh = read_sheet_hph(path, "散粮车")
        for h in ch:
            cont[h].add(ship)
        for h in bh:
            bulk[h].add(ship)
        print(f"  {ship}: {path.name} v{ver}  集装箱 {len(ch)}货票 | 散粮 {len(bh)}车")
    filed = set(latest)

    print("\n=== ① 集装箱(wagon_container_shipments)===")
    audit_one(hub, "wagon_container_shipments", cont, ydid2hph, ydid2date, filed, "箱")
    print("\n=== ② 散粮(wagon_shipments)===")
    audit_one(hub, "wagon_shipments", bulk, ydid2hph, ydid2date, filed, "车")
    if KNOWN_BOX_EXCEPTIONS:
        print(f"\n已知 box 级例外(不计不符,更正时 --exception 保留): {sorted(KNOWN_BOX_EXCEPTIONS)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="限定月份目录,如 2026-06;默认扫所有 2026 月")
    audit(ap.parse_args().month)


if __name__ == "__main__":
    main()
