from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cli_dashboard as dashboard  # noqa: E402


def _candidate_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE inspection_ingestion_candidates (candidate_status TEXT)"
    )
    conn.execute("CREATE TABLE release_batches (dispatch_status TEXT)")
    conn.executemany(
        "INSERT INTO inspection_ingestion_candidates VALUES (?)",
        [
            ("matched",),
            ("archived",),
            ("superseded",),
            ("matched_by_inference",),
            ("pending_review",),
            ("pending_review",),
            ("pending_match",),
            ("pending_freight_info",),
        ],
    )
    conn.executemany(
        "INSERT INTO release_batches VALUES (?)",
        [("pending_freight",), ("loading",)],
    )
    conn.commit()
    conn.close()


def test_candidate_counts_exclude_historical_terminal_states(tmp_path, monkeypatch):
    db_path = tmp_path / "sop.sqlite3"
    _candidate_db(db_path)
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)

    assert dashboard.query_pending_candidate_counts() == {
        "pending_freight_info": 1,
        "pending_match": 1,
        "pending_review": 2,
    }


def test_pending_release_notice_count_uses_pending_freight_batches(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / "sop.sqlite3"
    _candidate_db(db_path)
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)

    assert dashboard.query_pending_release_notice_count() == 1


def test_system_panel_keeps_zero_hanging_summaries_without_details(monkeypatch):
    monkeypatch.setattr(dashboard, "list_daemons", lambda: [])
    monkeypatch.setattr(dashboard, "query_pending_candidate_counts", lambda: {})
    monkeypatch.setattr(dashboard, "query_pending_release_notice_count", lambda: 0)

    panel = "\n".join(
        dashboard._strip_ansi(line) for line in dashboard.panel_system()
    )

    assert "挂起运单 0" in panel
    assert "挂起出港计划通知单 0" in panel
    assert "挂起运单明细" not in panel


def test_system_panel_shows_only_actionable_candidate_alerts(monkeypatch):
    monkeypatch.setattr(dashboard, "list_daemons", lambda: [])
    monkeypatch.setattr(
        dashboard,
        "query_pending_candidate_counts",
        lambda: {"pending_review": 2},
    )
    monkeypatch.setattr(dashboard, "query_pending_release_notice_count", lambda: 1)

    panel = "\n".join(
        dashboard._strip_ansi(line) for line in dashboard.panel_system()
    )

    assert "挂起运单 2" in panel
    assert "挂起出港计划通知单 1" in panel
    assert "挂起运单明细" in panel
    assert "pending_review 2" in panel
