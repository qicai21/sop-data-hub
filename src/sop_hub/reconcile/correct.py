"""更正(--apply):**自动只做安全的两件 + 每笔落日志**。

- reroute 错挂(MISMATCH):batch_id/ship_name 改回源指定 batch,重算聚合。可逆。
- spec 自定 backfill(九三集装箱:给新货从 95306 补 hph),不改归属。
- PHANTOM(删)/NEW(分船)**不自动做**,只写"待人工"日志 + 计数,交人工。

日志双写:`reconcile_action_log` 表(逐笔可查/可回溯)+ `runtime/reconcile.log` 文件。
"""
from __future__ import annotations

from pathlib import Path

from sop_hub.utils.time import now_iso_beijing

from .engine import MISMATCH, NEW_UNATTR, PHANTOM

LOG_FILE = Path("runtime/reconcile.log")
DB_PATH = "data/sop_agent.db"


def ensure_log_table(hub) -> None:
    hub.execute("""CREATE TABLE IF NOT EXISTS reconcile_action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at TEXT, project TEXT, leg TEXT, action TEXT,
        entity_key TEXT, from_value TEXT, to_value TEXT, source_ref TEXT )""")
    hub.commit()


class ReconcileLog:
    """逐笔写表 + 攒行写文件。"""

    def __init__(self, hub, run_at: str):
        self.hub = hub
        self.run_at = run_at
        self.buf: list[str] = []

    def act(self, project, leg, action, key, frm, to, src="daily"):
        self.hub.execute(
            "INSERT INTO reconcile_action_log "
            "(run_at,project,leg,action,entity_key,from_value,to_value,source_ref) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.run_at, project, leg, action, str(key), str(frm), str(to), src))

    def line(self, text):
        self.buf.append(text)

    def flush(self):
        self.hub.commit()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            for ln in self.buf:
                f.write(f"{self.run_at}  {ln}\n")


def _has_col(hub, table, col):
    return col in {r[1] for r in hub.execute(f"PRAGMA table_info({table})")}


def _batch_ship(hub, bid):
    r = hub.execute("SELECT ship_name FROM release_batches WHERE id=?", (bid,)).fetchone()
    return r[0] if r else None


def reroute_mismatch(spec, result, hub, log) -> int:
    """错挂 → 改归源 batch(+ ship_name 若有列),重算受影响批次聚合。"""
    rows = result.by_cat.get(MISMATCH, [])
    if not rows:
        return 0
    where = " AND ".join(f"{c}=?" for c in spec.key_cols)
    set_ship = _has_col(hub, spec.table, "ship_name")
    affected = set()
    for key, d, s in rows:
        vals = key if isinstance(key, tuple) else (key,)
        if set_ship:
            hub.execute(f"UPDATE {spec.table} SET batch_id=?, ship_name=? WHERE {where}",
                        (s, _batch_ship(hub, s), *vals))
        else:
            hub.execute(f"UPDATE {spec.table} SET batch_id=? WHERE {where}", (s, *vals))
        log.act(spec.project_id, spec.leg, "reroute", key, d, s)
        affected |= {d, s}
    hub.commit()
    try:  # 重算聚合(shipped_weight)
        from sop_hub.sop.shipped_weight import compute_for_release_batch
        for bid in affected:
            compute_for_release_batch(bid, db_path=DB_PATH)
    except Exception as e:
        log.line(f"  ⚠️ 重算聚合异常: {e}")
    log.line(f"[{spec.project_id}/{spec.leg}] reroute 错挂 {len(rows)} 箱 → 重算 {len(affected)} 批")
    return len(rows)


def gate_finished_ships(spec, result, hub, log) -> int:
    """完成闸:新货(NEW_UNATTR)若挂在**发完的船**上 → 移到当前唯一活跃船。
    无唯一活跃船 → 不动,告警(交人工)。移后仍是 NEW_UNATTR(源到再确认归属)。"""
    finished = spec.finished_batches(hub)
    on_finished = [(k, d) for k, d in result.by_cat.get(NEW_UNATTR, []) if d in finished]
    if not on_finished:
        return 0
    active = spec.active_batch(hub)
    if not active:
        log.line(f"[{spec.project_id}/{spec.leg}] ⚠️ 完成闸:{len(on_finished)} 新货挂在发完的船,"
                 f"但当前无唯一活跃船 → 待人工")
        return 0
    ship = _batch_ship(hub, active)
    set_ship = _has_col(hub, spec.table, "ship_name")
    where = " AND ".join(f"{c}=?" for c in spec.key_cols)
    affected = {active}
    for key, d in on_finished:
        vals = key if isinstance(key, tuple) else (key,)
        if set_ship:
            hub.execute(f"UPDATE {spec.table} SET batch_id=?, ship_name=? WHERE {where}",
                        (active, ship, *vals))
        else:
            hub.execute(f"UPDATE {spec.table} SET batch_id=? WHERE {where}", (active, *vals))
        log.act(spec.project_id, spec.leg, "gate_move", key, d, active)
        affected.add(d)
    hub.commit()
    try:
        from sop_hub.sop.shipped_weight import compute_for_release_batch
        for bid in affected:
            compute_for_release_batch(bid, db_path=DB_PATH)
    except Exception as e:
        log.line(f"  ⚠️ 重算聚合异常: {e}")
    log.line(f"[{spec.project_id}/{spec.leg}] 完成闸:{len(on_finished)} 新货移出发完的船 "
             f"→ 当前活跃船[{ship}](仍待源到确认)")
    return len(on_finished)


def log_needs_human(spec, result, hub, log) -> None:
    """phantom(待删)/ new(待分船)不自动做,记数 + 待人工日志。"""
    if result.n(PHANTOM):
        log.line(f"[{spec.project_id}/{spec.leg}] ⚠️ 待人工·幻影/错表 {result.n(PHANTOM)} —— 核实后删")
    if result.n(NEW_UNATTR):
        log.line(f"[{spec.project_id}/{spec.leg}] ⚠️ 待人工·新货源未到 {result.n(NEW_UNATTR)} —— 等额外源到再分船")


def delete_phantom(spec, result, hub, log) -> int:
    """删 phantom(95306本leg无的DB行)。**人工显式触发**(--delete-phantom),不进每日自动。
    删前把 batch_id 记进日志 from_value 供回溯;改前请先备份。"""
    keys = result.by_cat.get(PHANTOM, [])
    if not keys:
        return 0
    where = " AND ".join(f"{c}=?" for c in spec.key_cols)
    for key in keys:
        vals = key if isinstance(key, tuple) else (key,)
        row = hub.execute(f"SELECT batch_id FROM {spec.table} WHERE {where}", vals).fetchone()
        hub.execute(f"DELETE FROM {spec.table} WHERE {where}", vals)
        log.act(spec.project_id, spec.leg, "delete_phantom", key, row[0] if row else "", "")
    hub.commit()
    log.line(f"[{spec.project_id}/{spec.leg}] 删 phantom {len(keys)} 行 —— 人工确认删除")
    return len(keys)


def apply_corrections(spec, result, rail, hub, log) -> dict:
    n_re = reroute_mismatch(spec, result, hub, log)
    n_bf = spec.backfill(rail, hub, result, log)   # 项目自定(九三集装箱补 hph)
    n_gate = gate_finished_ships(spec, result, hub, log)  # 完成闸:新货移出发完的船
    log_needs_human(spec, result, hub, log)
    return {"rerouted": n_re, "backfilled": n_bf, "gated": n_gate,
            "phantom_left": result.n(PHANTOM), "new_left": result.n(NEW_UNATTR)}
