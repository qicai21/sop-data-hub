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
from sop_hub.reconcile import correct, jiusan_spec, marking  # noqa: E402
from sop_hub.utils.time import now_iso_beijing  # noqa: E402

HUB = "data/sop_agent.db"
REGISTRY = {"jiusan": jiusan_spec.SPECS}


def _blabel(hub, bid, cache={}):
    if bid not in cache:
        r = hub.execute("SELECT ship_name, batch_sequence FROM release_batches WHERE id=?", (bid,)).fetchone()
        cache[bid] = f"{r[0]}·{r[1]}" if r else (str(bid)[:8] if bid else "—")
    return cache[bid]


def _ydid_of(key):
    return key[0] if isinstance(key, tuple) else key


def run(project, leg_filter, apply, mark):
    specs = REGISTRY[project]
    rail = sqlite3.connect(jiusan_spec.RAIL)
    hub = sqlite3.connect(HUB)
    marking.ensure_columns(hub)        # 幂等建 reconciled_at / reconcile_source_ref
    correct.ensure_log_table(hub)      # 幂等建 reconcile_action_log
    log = correct.ReconcileLog(hub, now_iso_beijing())
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

        if apply:
            c = correct.apply_corrections(spec, res, rail, hub, log)
            print(f"  ▸ 更正: reroute={c['rerouted']} backfill={c['backfilled']} "
                  f"完成闸移={c['gated']} | 待人工 phantom={c['phantom_left']} new={c['new_left']}")
            res = reconcile(spec, rail, hub)   # 更正后重对账,供标核对完毕用

        if apply or mark:
            m = marking.commit_reconciled(spec, res, rail, hub, source_ref="daily")
            print(f"  ▸ 标核对完毕: ok={m['ok_marked']} + grandfather历史={m['grandfathered']} "
                  f"| 真phantom待删={m['true_phantom_left']}")

    log.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="jiusan")
    ap.add_argument("--leg", choices=["container", "bulk"])
    ap.add_argument("--apply", action="store_true",
                    help="自动 reroute 错挂 + backfill;phantom/new 只告警(写库+日志)")
    ap.add_argument("--mark", action="store_true",
                    help="把 ok 标核对完毕 + grandfather 历史船(写库)")
    a = ap.parse_args()
    run(a.project, a.leg, a.apply, a.mark)


if __name__ == "__main__":
    main()
