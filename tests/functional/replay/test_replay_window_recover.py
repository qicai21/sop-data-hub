"""Replay-driven inspection window_recover cases (Phase 3)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sop_hub.sop.inspection_window_recover import recover_loading_cars_via_window
from tests.support.replay import load_replay

pytestmark = [pytest.mark.functional, pytest.mark.replay]


def _seed_rail(db_path: Path, shipments: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE shipments ("
            " ydid TEXT PRIMARY KEY, car_no TEXT, destination_name TEXT,"
            " cargo_name TEXT, ticketed_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO shipments VALUES (?,?,?,?,?)",
            [
                (
                    s["ydid"],
                    s["car_no"],
                    s["destination_name"],
                    s.get("cargo_name") or "",
                    s["ticketed_at"],
                )
                for s in shipments
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_replay_window_recover_picks_current_batch(tmp_path: Path):
    case = load_replay("inspection/window_recover_anchor_old_ticket.json")
    rail = tmp_path / "rail.sqlite3"
    _seed_rail(rail, case["input"]["shipments"])

    r = recover_loading_cars_via_window(
        rail_db_path=rail,
        **case["input"]["recover_kwargs"],
    )
    exp = case["expected"]
    assert r["status"] == exp["status"]
    assert r["anchor_ticketed_at"] == exp["anchor_ticketed_at"]
    assert r["loading_car_nos"] == exp["loading_car_nos"]


def test_replay_window_recover_skips_old_ticket_when_new_absent(tmp_path: Path):
    case = load_replay("inspection/window_recover_anchor_old_ticket.json")
    rail = tmp_path / "rail.sqlite3"
    only_old = [s for s in case["input"]["shipments"] if s["ydid"].startswith("old-")]
    _seed_rail(rail, only_old)

    r = recover_loading_cars_via_window(
        rail_db_path=rail,
        **case["input"]["recover_kwargs"],
    )
    exp = case["expected_when_new_deleted"]
    assert r["status"] == exp["status"]
    assert r["anchor_car_no"] is exp["anchor_car_no"]
