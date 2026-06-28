"""通用对账 CLI(第一阶段:只读审计)。

  python scripts/run_reconcile.py --project jiusan [--leg container|bulk]

跑通用引擎(95306全集 × 额外源 × DB 三方),按五分类出报告。--apply(更正)
+ 核对完毕标记 + 定时,在引擎验证通过后接着实现。
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sop_hub.reconcile import (  # noqa: E402
    MISMATCH, MISSING, NEW_UNATTR, OK, PHANTOM, mismatch_breakdown, reconcile,
)
from sop_hub.reconcile import jiusan_spec  # noqa: E402

HUB = "data/sop_agent.db"
REGISTRY = {"jiusan": jiusan_spec.SPECS}


def _blabel(hub, bid, cache={}):
    if bid not in cache:
        r = hub.execute("SELECT ship_name, batch_sequence FROM release_batches WHERE id=?", (bid,)).fetchone()
        cache[bid] = f"{r[0]}·{r[1]}" if r else (str(bid)[:8] if bid else "—")
    return cache[bid]


def _ydid_of(key):
    return key[0] if isinstance(key, tuple) else key


def run(project, leg_filter):
    specs = REGISTRY[project]
    rail = sqlite3.connect(jiusan_spec.RAIL)
    hub = sqlite3.connect(HUB)
    for leg, cls in specs.items():
        if leg_filter and leg != leg_filter:
            continue
        spec = cls()
        res = reconcile(spec, rail, hub)
        U, D = spec.universe(rail), spec.db_rows(hub)
        unit = "箱" if leg == "container" else "车"
        print("\n" + "=" * 64)
        print(res.summary_line())

        # ① 错挂(应≠实)
        mb = mismatch_breakdown(res, hub)
        if mb:
            print("  ── 错挂(应≠实,改归源)──")
            for (s, d), n in mb.most_common():
                hph = len({_ydid_of(k) for k, dd, ss in res.by_cat[MISMATCH] if (ss, dd) == (s, d)})
                print(f"     应[{_blabel(hub, s)}] / 实[{_blabel(hub, d)}]: {n}{unit} / {hph}货票")

        # ② 漏入库
        if res.n(MISSING):
            bd = Counter(_blabel(hub, s) for _k, s in res.by_cat[MISSING])
            print(f"  ── 漏入库 {res.n(MISSING)}{unit} ──  {dict(bd)}")

        # ③ 新货(源未到)by 实挂船+日期
        if res.n(NEW_UNATTR):
            bd = Counter((_blabel(hub, d), U.get(k, {}).get("date", "?"))
                         for k, d in res.by_cat[NEW_UNATTR])
            print(f"  ── 新货·源未到 {res.n(NEW_UNATTR)}{unit}(入库挂pending,不猜归属)──")
            for (shipb, dt), n in sorted(bd.items()):
                print(f"     实挂[{shipb}] {dt}: {n}{unit}")

        # ④ 幻影/错表(95306本leg无、DB有)
        if res.n(PHANTOM):
            bd = Counter(_blabel(hub, D.get(k)) for k in res.by_cat[PHANTOM])
            print(f"  ── 幻影/错表 {res.n(PHANTOM)}{unit}(95306本leg无 → 删)──  {dict(bd)}")

        print(f"  ✓ 核对完毕(一致): {res.n(OK)}{unit}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="jiusan")
    ap.add_argument("--leg", choices=["container", "bulk"])
    a = ap.parse_args()
    run(a.project, a.leg)


if __name__ == "__main__":
    main()
