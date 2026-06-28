"""通用对账框架:95306(全集) × 额外源(归属) × DB(现状) 三方对齐。

engine.reconcile(spec, rail, hub) -> ReconcileResult(五分类)。
每项目一个 ReconcileSpec;九三是第一个实例(jiusan_spec)。
"""
from .engine import (  # noqa: F401
    CATEGORIES,
    MISMATCH,
    MISSING,
    NEW_UNATTR,
    OK,
    PHANTOM,
    ReconcileResult,
    ReconcileSpec,
    mismatch_breakdown,
    reconcile,
)
