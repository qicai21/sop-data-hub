"""「核对完毕」状态:落 `reconciled_at` / `reconcile_source_ref`。

- 每日跑完把 `ok` 标核对完毕(带源版本)→ 下次 db_rows 自动跳过(幂等)。
- 历史船(phantom 但 ydid 是本 leg 真车)grandfather 封存,信任当年对账。
- mismatch / new / 真phantom(错 leg、95306 无)**不标**,留待更正。
"""
from __future__ import annotations

from sop_hub.utils.time import now_iso_beijing

from .engine import OK, PHANTOM

_COLS = [("reconciled_at", "TEXT"), ("reconcile_source_ref", "TEXT")]
_TABLES = ["wagon_shipments", "wagon_container_shipments"]


def ensure_columns(hub) -> None:
    """幂等加 reconciled_at / reconcile_source_ref 两列。"""
    for t in _TABLES:
        have = {r[1] for r in hub.execute(f"PRAGMA table_info({t})")}
        for col, typ in _COLS:
            if col not in have:
                hub.execute(f"ALTER TABLE {t} ADD COLUMN {col} {typ}")
    hub.commit()


def _mark(hub, table, key_cols, keys, source_ref, ts) -> int:
    where = " AND ".join(f"{c}=?" for c in key_cols)
    n = 0
    for k in keys:
        vals = k if isinstance(k, tuple) else (k,)
        n += hub.execute(
            f"UPDATE {table} SET reconciled_at=?, reconcile_source_ref=? "
            f"WHERE {where} AND reconciled_at IS NULL", (ts, source_ref, *vals)).rowcount
    hub.commit()
    return n


def _key_ydid(k):
    return k[0] if isinstance(k, tuple) else k


def commit_reconciled(spec, result, rail, hub, source_ref: str) -> dict:
    """标 ok + grandfather 历史 phantom。返回计数。"""
    ts = now_iso_beijing()
    n_ok = _mark(hub, spec.table, spec.key_cols, result.by_cat.get(OK, []), f"ok:{source_ref}", ts)

    # grandfather:phantom 里 ydid 属本 leg 真车(不卡日期)→ 历史船,封存
    leg_ydids = spec.leg_ydids_all_dates(rail)
    gf = [k for k in result.by_cat.get(PHANTOM, []) if _key_ydid(k) in leg_ydids]
    n_gf = _mark(hub, spec.table, spec.key_cols, gf, "grandfather_historical", ts)

    return {
        "ok_marked": n_ok,
        "grandfathered": n_gf,
        "true_phantom_left": result.n(PHANTOM) - len(gf),  # 错leg/95306无 → 待删
    }
