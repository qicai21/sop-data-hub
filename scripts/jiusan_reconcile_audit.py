"""九三集装箱货票自动对账 —— 第一阶段:审查(只读)。

自动扫 WeChat `msg/file/<月>/` 文件夹 → 找各船**最新版**货票 xlsx(新台子 sheet)
→ 按货票号(hph)建"应船"权威 → 逐箱比对 DB 现挂船(实船)→ 出「应船 vs 实船」
不符报告。**纯只读、不写库**,可反复跑。

这是自动对账功能(工单 2026-06-27-九三大豆货票自动对账)的第一阶段。更正(写台账+
重路由)复用 `reconcile_jiusan_containers.py --apply`,本脚本只负责"审查发现不符"。

用法:
  python scripts/jiusan_reconcile_audit.py            # 全部船审计
  python scripts/jiusan_reconcile_audit.py --month 2026-06

复用 reconcile_jiusan_containers 的 read_file_hph / load_ydid2hph,口径完全一致。
"""
import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import reconcile_jiusan_containers as rec  # noqa: E402  复用解析/桥接内核

HUB = "data/sop_agent.db"
PROJECT = "jiusan"

# 一票多船的已知 box 级例外(在 hph 级审计里不算"不符",更正时走 --exception)。
KNOWN_BOX_EXCEPTIONS = {"GZDJW0496546"}


def _wx_file_dirs(month_filter: str | None):
    """WeChat 文件夹按月滚动;wxid 可能变,glob 兜底。返回所有月目录(新→旧)。"""
    base = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    pat = f"*/msg/file/{month_filter}" if month_filter else "*/msg/file/2026-*"
    return sorted(base.glob(pat), reverse=True)


def _parse_ship_version(fname: str):
    """'26.6.20大豆 诚信 货票(5).xlsx' → ('诚信', 5)。无版本号 → 0。"""
    m = re.search(r"大豆\s*(\S+?)\s*货票", fname)
    if not m:
        return None, 0
    v = re.search(r"\((\d+)\)", fname)
    return m.group(1).strip(), (int(v.group(1)) if v else 0)


def find_latest_container_tickets(month_filter: str | None):
    """ship -> (Path, version, mtime),跨月取版本号最大、同版本取 mtime 最新。"""
    best: dict[str, tuple] = {}
    for d in _wx_file_dirs(month_filter):
        for f in d.glob("*大豆*货票*.xlsx"):
            ship, ver = _parse_ship_version(f.name)
            if not ship:
                continue
            key = (ver, f.stat().st_mtime)
            if ship not in best or key > (best[ship][1], best[ship][2]):
                best[ship] = (f, ver, f.stat().st_mtime)
    return best


def audit(month_filter: str | None):
    latest = find_latest_container_tickets(month_filter)
    if not latest:
        print("未在 WeChat 文件夹找到任何『大豆…货票』xlsx。"); return

    rail = sqlite3.connect(rec.RAIL)
    hub = sqlite3.connect(HUB)
    ydid2hph = rec.load_ydid2hph(rail)

    # 各船最新文件 → hph 集合;hph -> {出现于哪些船文件}(>1 = box级例外候选)
    hph2ships: dict[str, set] = defaultdict(set)
    print("=== 权威文件(各船最新货票)===")
    for ship, (path, ver, _mt) in sorted(latest.items()):
        try:
            hphs = rec.read_file_hph(path)
        except Exception as e:
            print(f"  ⚠️ {path.name}: 读取失败 {e}"); continue
        for h in hphs:
            hph2ships[h].add(ship)
        print(f"  {ship}: {path.name}  v{ver}  → {len(hphs)} 货票")

    # 逐箱比对:九三所有集装箱 lot01 的箱(实船 = 其 batch 的 ship_name)
    boxes = hub.execute(
        "SELECT wcs.box_no, wcs.ydid, rb.ship_name "
        "FROM wagon_container_shipments wcs JOIN release_batches rb ON wcs.batch_id=rb.id "
        "WHERE rb.project=? AND rb.batch_sequence='lot01'", (PROJECT,)).fetchall()

    # (应船,实船) -> {hph}, 箱数
    mismatch_hph: dict[tuple, set] = defaultdict(set)
    mismatch_box: dict[tuple, int] = defaultdict(int)
    multi_ship = []   # hph 同时出现在多船文件(需 box 级)
    no_authority = 0  # 该箱货票号不在任何当前文件 → 无权威,跳过
    exc_count = 0     # 已知 box 级例外箱(不计入不符)
    ok = 0
    for box, ydid, cur_ship in boxes:
        hph = ydid2hph.get(ydid)
        # 已知一票多船例外:box 级人工拆,hph 级审计无法判,直接跳过不算不符
        if hph in KNOWN_BOX_EXCEPTIONS:
            exc_count += 1; continue
        ships = hph2ships.get(hph)
        if not ships:
            no_authority += 1; continue
        if len(ships) > 1:
            multi_ship.append((hph, box, cur_ship, sorted(ships))); continue
        should = next(iter(ships))
        if should != cur_ship:
            mismatch_hph[(should, cur_ship)].add(hph)
            mismatch_box[(should, cur_ship)] += 1
        else:
            ok += 1

    print("\n=== 不符(应船 ≠ 实船,需更正)===")
    if not mismatch_box:
        print("  ✓ 无不符,各箱归船与港方货票一致。")
    else:
        for (should, cur), nbox in sorted(mismatch_box.items(), key=lambda x: -x[1]):
            print(f"  应【{should}】/ 实【{cur}】: {nbox} 箱 / {len(mismatch_hph[(should, cur)])} 货票")
            print(f"      更正命令: python scripts/reconcile_jiusan_containers.py "
                  f"--file <{should}最新货票.xlsx> --ship {should} --other-ship {cur} --apply")

    if multi_ship:
        print(f"\n=== ⚠️ 一票多船(需 box 级 --exception,{len(multi_ship)} 箱)===")
        for hph, box, cur, ships in multi_ship[:10]:
            print(f"  {hph} box={box} 现挂{cur},文件出现于 {ships}")

    print(f"\n=== 小结 ===")
    print(f"  审计箱总数: {len(boxes)}  |  一致: {ok}  |  不符: {sum(mismatch_box.values())}  "
          f"|  无权威文件跳过: {no_authority}  |  已知例外: {exc_count}")
    if KNOWN_BOX_EXCEPTIONS:
        print(f"  已知 box 级例外(不计入不符,更正时 --exception 保留): {sorted(KNOWN_BOX_EXCEPTIONS)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="限定月份目录,如 2026-06;默认扫所有 2026 月")
    audit(ap.parse_args().month)


if __name__ == "__main__":
    main()
