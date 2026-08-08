from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "jiusan_morning_report_ingest",
    REPO / "scripts" / "jiusan_morning_report_ingest.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_cycle_pool_counts_boxes_by_latest_ticketed_date():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE wagon_container_shipments "
        "(project_id TEXT, box_no TEXT, ticketed_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO wagon_container_shipments VALUES (?,?,?)",
        [
            ("jiusan", "TBJU0000001", "2026-06-20 10:00:00"),
            ("jiusan", "TBJU0000001", "2026-07-11 10:00:00"),
            ("jiusan", "TBJU0000002", "2026-07-09 10:00:00"),
            ("jiusan", "TBJU0000003", "2026-07-10 10:00:00"),
            ("other_project", "TBJU0000004", "2026-07-15 10:00:00"),
        ],
    )

    assert m.compute_cycle_pool_unique(conn) == 2
