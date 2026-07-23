from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cli_dashboard as dashboard  # noqa: E402


def _rail_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE shipments (
            ydid TEXT,
            origin_name TEXT,
            destination_name TEXT,
            cargo_name TEXT,
            accepted_at TEXT,
            ticketed_at TEXT,
            status_code TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO shipments VALUES (?,?,?,?,?,?,?)",
        [
            # 吉林金钢:两辆承认车；重复 ydid 不能重复计数。
            ("j1", "高桥镇", "四平", "铁矿粉", "2026-07-02 15:01:00", None, "22"),
            ("j1", "高桥镇", "四平", "铁矿粉", "2026-07-02 15:01:00", "", "22"),
            ("j2", "高桥镇", "四平", "铁矿粉", "2026-07-03 15:02:00", "", "22"),
            # 已制票、状态已推进、上月、错误货物和错误到站均不计。
            ("j3", "高桥镇", "四平", "铁矿粉", "2026-07-03 15:02:00", "2026-07-04 08:00:00", "22"),
            ("j4", "高桥镇", "四平", "铁矿粉", "2026-07-03 15:02:00", None, "35"),
            ("j5", "高桥镇", "四平", "铁矿粉", "2026-06-30 15:02:00", None, "22"),
            ("j6", "高桥镇", "四平", "氧化铝", "2026-07-03 15:02:00", None, "22"),
            ("j7", "高桥镇", "汐子", "铁矿粉", "2026-07-03 15:02:00", None, "22"),
            # 九三项目独立统计。
            ("s1", "高桥镇", "新台子", "大豆", "2026-07-05 15:03:00", None, "22"),
        ],
    )
    conn.commit()
    conn.close()


def test_reserved_wagons_are_current_month_unticketed_distinct_ydids(tmp_path):
    db_path = tmp_path / "rail.sqlite3"
    _rail_db(db_path)
    scopes = {
        "jilin_jingang_jinzhou": {
            "origin": "高桥镇",
            "destination": "四平",
            "cargo_names": ("铁矿粉", "镍矿"),
        },
        "jiusan": {
            "origin": "高桥镇",
            "destination": "新台子",
            "cargo_names": ("大豆",),
        },
    }

    counts = dashboard.query_reserved_wagon_counts(
        db_path=db_path,
        now=datetime(2026, 7, 23, 15, 0),
        scopes=scopes,
    )

    assert counts == {
        "jilin_jingang_jinzhou": 2,
        "jiusan": 1,
    }


def test_project_title_replaces_internal_id_with_reserved_wagons():
    panel = dashboard.panel_project(
        "jilin_jingang_jinzhou",
        [],
        reserved_wagons=212,
    )
    title = dashboard._strip_ansi(panel[0])

    assert "吉林金钢(锦州)" in title
    assert "承认车 212" in title
    assert "[jilin_jingang_jinzhou]" not in title


def test_missing_rail_database_keeps_dashboard_available(tmp_path):
    counts = dashboard.query_reserved_wagon_counts(
        db_path=tmp_path / "missing.sqlite3",
        now=datetime(2026, 7, 23, 15, 0),
        scopes={},
    )

    assert counts == {}
    title = dashboard._strip_ansi(
        dashboard.panel_project("chaoyang_steel", [], reserved_wagons=None)[0]
    )
    assert "承认车 —" in title
