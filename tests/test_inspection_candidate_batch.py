from __future__ import annotations

import sqlite3

from sop_hub.sop.inspection_candidate_batch import (
    candidate_loading_cars_already_persisted,
    resolve_preassigned_open_batch,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE release_batches (
          id TEXT PRIMARY KEY,
          project TEXT,
          ship_name TEXT,
          destination_station TEXT,
          dispatch_status TEXT
        );
        CREATE TABLE wagon_shipments (
          batch_id TEXT,
          car_no TEXT
        );
        """
    )
    return conn


def test_preassigned_open_batch_survives_later_batch_creation():
    conn = _db()
    conn.executemany(
        "INSERT INTO release_batches VALUES (?,?,?,?,?)",
        [
            ("lot5", "zhongtang_special_steel", "宝丽", "汐子", "loading"),
            ("lot6", "zhongtang_special_steel", "宝丽", "汐子", "enriched"),
        ],
    )
    candidate = {"release_batch_id": "lot5"}

    assert resolve_preassigned_open_batch(
        conn,
        candidate,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
    ) == "lot5"


def test_preassigned_closed_or_mismatched_batch_is_not_reused():
    conn = _db()
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?,?,?)",
        ("lot5", "zhongtang_special_steel", "宝丽", "汐子", "all_loaded"),
    )
    candidate = {"release_batch_id": "lot5"}

    assert resolve_preassigned_open_batch(
        conn,
        candidate,
        project_id="zhongtang_special_steel",
        ship_name="宝丽",
        destination="汐子",
    ) is None


def test_completed_candidate_is_detected_from_all_loading_cars():
    conn = _db()
    conn.executemany(
        "INSERT INTO wagon_shipments VALUES (?,?)",
        [("lot8", "1000001"), ("lot8", "1000002"), ("other", "1000003")],
    )
    candidate = {"release_batch_id": "lot8"}

    assert candidate_loading_cars_already_persisted(
        conn, candidate, ["1000001", "1000002"],
    )
    assert not candidate_loading_cars_already_persisted(
        conn, candidate, ["1000001", "1000003"],
    )
