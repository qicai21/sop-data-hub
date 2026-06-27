"""发货匹配规则(find_release_batch_with_reason)守门 —— fixture 化 tmp DB。

替代被删的 test_data_agent 里那批发货匹配用例(走 legacy data_agent、断言旧行为)。
这里直测当前发车链实际用的 executor_runner.find_release_batch_with_reason:
  - 只有 open(enriched/loading)批次才自动匹配,loading 优先;
  - 已发完(all_loaded/confirmed_received…)→ all_loaded_full,不再自动匹配;
  - 船名找不到 → ship_not_found;有批次但都非 open → no_open_lot。
2026-06-17。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop.executor_runner import find_release_batch_with_reason


def _seed(db: str, batches: list[tuple]):
    """batches: (id, ship_name, dispatch_status, destination_station, project)。"""
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE release_batches(id TEXT, ship_name TEXT, "
                 "dispatch_status TEXT, destination_station TEXT, project TEXT, "
                 "batch_sequence TEXT)")
    conn.executemany("INSERT INTO release_batches"
                     "(id, ship_name, dispatch_status, destination_station, project) "
                     "VALUES(?,?,?,?,?)", batches)
    conn.commit()
    conn.close()


def test_active_loading_batch_matches(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "马兰希望", "loading", "四平", "jilin_jingang_jinzhou")])
    batch, reason, cands = find_release_batch_with_reason("马兰希望", "四平", db_path=db)
    assert reason == "ok" and batch["id"] == "b1"


def test_loading_preferred_over_enriched(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b_enr", "马兰希望", "enriched", "四平", "p"),
               ("b_load", "马兰希望", "loading", "四平", "p")])
    batch, reason, _ = find_release_batch_with_reason("马兰希望", "四平", db_path=db)
    assert reason == "ok" and batch["id"] == "b_load"  # loading 优先填满


def test_completed_batch_no_longer_auto_matches(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "马兰希望", "confirmed_received", "四平", "p")])
    batch, reason, cands = find_release_batch_with_reason("马兰希望", "四平", db_path=db)
    assert batch is None and reason == "all_loaded_full" and cands == ["b1"]


def test_all_loaded_returns_candidates_for_pending(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "宝腾海", "all_loaded", "朝阳西", "p"),
               ("b2", "宝腾海", "tracking", "朝阳西", "p")])
    batch, reason, cands = find_release_batch_with_reason("宝腾海", "朝阳西", db_path=db)
    assert batch is None and reason == "all_loaded_full"
    assert set(cands) == {"b1", "b2"}


def test_ship_not_found(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "马兰希望", "loading", "四平", "p")])
    batch, reason, cands = find_release_batch_with_reason("不存在的船", "四平", db_path=db)
    assert batch is None and reason == "ship_not_found" and cands == []


def test_has_batch_but_no_open_lot(tmp_path):
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "马兰希望", "pending_freight", "四平", "p")])
    batch, reason, cands = find_release_batch_with_reason("马兰希望", "四平", db_path=db)
    assert batch is None and reason == "no_open_lot" and cands == ["b1"]


def test_ship_only_fallback_when_dest_mismatch(tmp_path):
    # ship 匹配但到站对不上 → 退回 ship-only,仍能按状态判定
    db = str(tmp_path / "sop.db")
    _seed(db, [("b1", "马兰希望", "loading", "新台子", "p")])
    batch, reason, _ = find_release_batch_with_reason("马兰希望", "四平", db_path=db)
    assert reason == "ok" and batch["id"] == "b1"
