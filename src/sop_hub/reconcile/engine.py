"""通用对账引擎:**95306(全集) × 额外源(归属) × DB(现状)** 三方对齐。

任何项目的对账都归约到同一个三方真值表(见 README/工单)。引擎只写一次,每项目
提供一个 `ReconcileSpec`(声明怎么圈 95306 全集、怎么读 DB 现状、额外源把每个 key
归到哪个 `release_batch_id`),引擎做三方 join + 判定 → 五分类结果。

判定锚 = **release_batch_id**(不是船名——船跨项目/跨 lot 复用,船名拆不干净;
batch_id 唯一编码 项目+船+lot+这一趟)。

key 由 spec 决定:散粮=ydid;集装箱=(ydid, box_no)(box 级,容一票两船拆分)。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# ── 五分类(真值表)────────────────────────────────────────────────
OK = "ok"                        # 95306有 · 源有 · DB归属==源     → 核对完毕
MISMATCH = "mismatch"            # 95306有 · 源有 · DB归属!=源     → 改归源(错挂)
MISSING = "missing"              # 95306有 · 源有 · DB无           → 入库+归属
NEW_UNATTR = "new_unattributed"  # 95306有 · 源未到               → 入库但挂pending,不猜
PHANTOM = "phantom"              # 95306无(或不属本leg/scope)· DB有 → 从DB删(幻影/错表/双写)

CATEGORIES = [OK, MISMATCH, MISSING, NEW_UNATTR, PHANTOM]


class ReconcileSpec:
    """每项目一份。子类实现三个取数方法,key 口径三方必须一致。"""

    project_id: str = ""
    leg: str = ""  # container / bulk / ...

    def universe(self, rail) -> dict:
        """95306 本项目本 leg 的真实全集:{key: {date, ...}}(已按 scope 过滤)。"""
        raise NotImplementedError

    def db_rows(self, hub) -> dict:
        """DB 现状:{key: 当前 release_batch_id}。"""
        raise NotImplementedError

    def source_batch(self, rail, hub) -> dict:
        """额外源解析 + 归属解析:{key: 应归 release_batch_id}。
        只含"源能定到 batch"的 key;源里没有的 key 不出现(→ 引擎判 NEW_UNATTR)。"""
        raise NotImplementedError


@dataclass
class ReconcileResult:
    project_id: str
    leg: str
    by_cat: dict = field(default_factory=dict)   # category -> [key | (key, db, src)]
    universe_n: int = 0
    db_n: int = 0
    source_n: int = 0

    def n(self, cat) -> int:
        return len(self.by_cat.get(cat, []))

    def summary_line(self) -> str:
        c = " | ".join(f"{cat}={self.n(cat)}" for cat in CATEGORIES)
        return (f"[{self.project_id}/{self.leg}] 95306全集={self.universe_n} "
                f"DB={self.db_n} 源={self.source_n}  → {c}")


def reconcile(spec: ReconcileSpec, rail, hub) -> ReconcileResult:
    """三方 join + 真值表判定。纯计算,只读,不写库。"""
    U = spec.universe(rail)
    D = spec.db_rows(hub)
    S = spec.source_batch(rail, hub)

    by_cat: dict[str, list] = {c: [] for c in CATEGORIES}
    for k in set(U) | set(D) | set(S):
        in_u = k in U
        d = D.get(k)        # 当前 batch_id(DB 没有则 None)
        s = S.get(k)        # 应归 batch_id(源没有则 None)
        if d is not None and not in_u:
            by_cat[PHANTOM].append(k)                 # DB 有、95306 本 leg 无 → 幻影/错表
        elif in_u and s is not None and d is not None:
            by_cat[OK if d == s else MISMATCH].append(k if d == s else (k, d, s))
        elif in_u and s is not None and d is None:
            by_cat[MISSING].append((k, s))            # 该入库
        elif in_u and s is None:
            by_cat[NEW_UNATTR].append((k, d))         # 新货,源未到(d 可能是默认堆的)
        # in_u and not present anywhere else 不会发生(k 来自三集合并集)

    return ReconcileResult(spec.project_id, spec.leg, by_cat,
                           universe_n=len(U), db_n=len(D), source_n=len(S))


def mismatch_breakdown(result: ReconcileResult, hub) -> Counter:
    """把 MISMATCH 按 (应batch→实batch) 计数,用于人看的报告。"""
    c = Counter()
    for _k, d, s in result.by_cat.get(MISMATCH, []):
        c[(s, d)] += 1
    return c
