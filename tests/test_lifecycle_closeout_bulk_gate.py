"""#issue-20260621:散粮 lot 计划吨位闸,防 lifecycle_closeout 误判完成。

散粮 lot(无 dispatch_plan)按吨位增量装,趟间"当前车全交付"≠ lot 装完。
诚信 lot02(5096/17000t,30%)曾被 closeout 误推 confirmed_received、从看板
loading 消失。守门:① 散粮欠装(剩余>容差)+全收货 → 不推(held_underfilled);
② 散粮足额/超发 → 正常推;③ 集装箱 lot(有 dispatch_plan)按箱管满,不受吨位闸。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sop_hub.sop.lifecycle_closeout import run_lifecycle_closeout


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.db"
    c = sqlite3.connect(str(db))
    c.execute(
        "CREATE TABLE release_batches (id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, "
        "batch_sequence TEXT, dispatch_status TEXT, dispatch_status_note TEXT, "
        "dispatch_status_updated_at TEXT, remaining_weight_tons REAL, batch_quantity REAL, "
        "updated_at TEXT)"
    )
    c.execute(
        "CREATE TABLE wagon_shipments (id TEXT, batch_id TEXT, latest_stage_key TEXT)"
    )
    c.execute(
        "CREATE TABLE wagon_container_shipments (id TEXT, batch_id TEXT, "
        "latest_stage_key TEXT, ticketed_at TEXT)"
    )
    c.execute(
        "CREATE TABLE release_batch_dispatch_plan (release_batch_id TEXT, planned_box_count INT)"
    )
    c.commit()
    c.close()
    return db


def _add_batch(db: Path, bid: str, *, remaining: float | None, has_plan: bool, n_wagons: int = 3):
    c = sqlite3.connect(str(db))
    c.execute(
        "INSERT INTO release_batches (id, project, ship_name, batch_sequence, dispatch_status, "
        "dispatch_status_note, remaining_weight_tons, batch_quantity) "
        "VALUES (?, 'jiusan', 'S', 'lot02', 'loading', '', ?, 17000)",
        (bid, remaining),
    )
    for i in range(n_wagons):  # 全部 latest_stage_key=delivered → all_received
        c.execute("INSERT INTO wagon_shipments VALUES (?,?, 'delivered')", (f"{bid}_w{i}", bid))
    if has_plan:
        c.execute("INSERT INTO release_batch_dispatch_plan VALUES (?, 92)", (bid,))
    c.commit()
    c.close()


def _status(db: Path, bid: str) -> str:
    c = sqlite3.connect(str(db))
    s = c.execute("SELECT dispatch_status FROM release_batches WHERE id=?", (bid,)).fetchone()[0]
    c.close()
    return s


def test_bulk_underfilled_held(tmp_path):
    """散粮欠装(剩 11904t)+ 全车收货 → 不推完成,held_underfilled。"""
    db = _make_db(tmp_path)
    _add_batch(db, "bulk_under", remaining=11904.0, has_plan=False)
    r = run_lifecycle_closeout(db_path=db)
    assert r["held_underfilled"] == 1
    assert r["advanced"] == 0
    assert _status(db, "bulk_under") == "loading"


def test_bulk_full_advances(tmp_path):
    """散粮足额/超发(剩 -194t)+ 全车收货 → 正常推 confirmed_received。"""
    db = _make_db(tmp_path)
    _add_batch(db, "bulk_full", remaining=-194.0, has_plan=False)
    r = run_lifecycle_closeout(db_path=db)
    assert r["held_underfilled"] == 0
    assert r["advanced"] == 1
    assert _status(db, "bulk_full") == "confirmed_received"


def test_container_not_gated_by_tonnage(tmp_path):
    """集装箱 lot(有 dispatch_plan)即使吨位剩很多,按箱管满,不受吨位闸 → 全收货即推。"""
    db = _make_db(tmp_path)
    _add_batch(db, "container_lot", remaining=5000.0, has_plan=True)
    r = run_lifecycle_closeout(db_path=db)
    assert r["held_underfilled"] == 0
    assert r["advanced"] == 1
    assert _status(db, "container_lot") == "confirmed_received"


def _add_container_batch(db: Path, bid: str, *, last_ticketed: str, n: int = 4):
    """集装箱批:wagon_container_shipments 全交付,ticketed_at=last_ticketed。"""
    c = sqlite3.connect(str(db))
    c.execute(
        "INSERT INTO release_batches (id, project, ship_name, batch_sequence, dispatch_status, "
        "dispatch_status_note, remaining_weight_tons, batch_quantity) "
        "VALUES (?, 'jiusan', 'C', 'lot01', 'loading', '', -100, 0)", (bid,))
    for i in range(n):
        c.execute("INSERT INTO wagon_container_shipments VALUES (?,?, 'delivered', ?)",
                  (f"{bid}_b{i}", bid, last_ticketed))
    c.commit()
    c.close()


def test_container_all_delivered_quiet_advances(tmp_path):
    """集装箱批全交付 + 静默(最近2天无新车)→ 推 confirmed_received。

    根治"closeout 只查 wagon_shipments、集装箱批永远关不了"(和谐1 lot01 卡 loading)。
    """
    db = _make_db(tmp_path)
    _add_container_batch(db, "cont_done", last_ticketed="2026-01-01")  # 远古 → 静默
    r = run_lifecycle_closeout(db_path=db)
    assert r["advanced"] == 1
    assert _status(db, "cont_done") == "confirmed_received"


def test_container_recent_held_active(tmp_path):
    """集装箱批全交付但最近2天还在制票(活跃船趟间空档)→ 不关,held_active。"""
    from datetime import datetime
    db = _make_db(tmp_path)
    _add_container_batch(db, "cont_active", last_ticketed=datetime.now().strftime("%Y-%m-%d"))
    r = run_lifecycle_closeout(db_path=db)
    assert r.get("held_active") == 1
    assert r["advanced"] == 0
    assert _status(db, "cont_active") == "loading"
