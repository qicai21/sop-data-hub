"""Active ship discovery for periodic 95306 status sync."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sop_hub.sop.active_status_sync import (
    discover_active_ships,
    run_active_status_sync,
)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "sop.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE release_batches ("
        "id TEXT PRIMARY KEY, project TEXT, ship_name TEXT, dispatch_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?)",
        [
            ("a", "zhongtang_special_steel", "宝丽", "loading"),
            ("b", "zhongtang_special_steel", "宝丽", "enriched"),
            ("c", "chaoyang_steel", "马兰幸福", "loading"),
            ("d", "chaoyang_steel", "马兰幸福", "closed"),
            ("e", "jilin_jingang_jinzhou", "马兰希望", "all_loaded"),
            ("f", "other", "X", "closed"),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_discover_active_ships_groups_by_project_ship(tmp_path):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    ships = discover_active_ships(conn)
    conn.close()
    keys = {(s.project_id, s.ship_name) for s in ships}
    assert ("zhongtang_special_steel", "宝丽") in keys
    assert ("chaoyang_steel", "马兰幸福") in keys
    assert ("jilin_jingang_jinzhou", "马兰希望") in keys
    # closed-only ship not listed
    assert ("other", "X") not in keys
    baoli = next(s for s in ships if s.ship_name == "宝丽")
    assert baoli.lot_count == 2


def test_discover_filter_projects(tmp_path):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    ships = discover_active_ships(conn, projects=["chaoyang_steel"])
    conn.close()
    assert len(ships) == 1
    assert ships[0].ship_name == "马兰幸福"


def test_run_active_status_sync_dry_run_writes_report(tmp_path, monkeypatch):
    db = _db(tmp_path)
    report_dir = tmp_path / "reports"
    # fake rail path missing → error ship entry, still writes report
    rail = tmp_path / "missing.sqlite3"
    report = run_active_status_sync(
        sop_db=db,
        rail_db=rail,
        dry_run=True,
        report_dir=report_dir,
    )
    assert report.report_path
    assert Path(report.report_path).exists()
    data = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert data["dry_run"] is True
    assert data["totals"]["errors"] >= 1
