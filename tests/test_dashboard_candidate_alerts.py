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
        ],
    )
    conn.commit()
    conn.close()


def test_candidate_counts_exclude_historical_terminal_states(tmp_path, monkeypatch):
    db_path = tmp_path / "sop.sqlite3"
    _candidate_db(db_path)
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)

    assert dashboard.query_pending_candidate_counts() == {
        "pending_match": 1,
        "pending_review": 2,
    }


def test_system_panel_hides_candidate_section_when_nothing_is_pending(monkeypatch):
    monkeypatch.setattr(dashboard, "list_daemons", lambda: [])
    monkeypatch.setattr(dashboard, "query_pending_candidate_counts", lambda: {})

    panel = "\n".join(
        dashboard._strip_ansi(line) for line in dashboard.panel_system()
    )

    assert "检装车候选" not in panel
    assert "待处理检装车" not in panel


def test_system_panel_shows_only_actionable_candidate_alerts(monkeypatch):
    monkeypatch.setattr(dashboard, "list_daemons", lambda: [])
    monkeypatch.setattr(
        dashboard,
        "query_pending_candidate_counts",
        lambda: {"pending_review": 2},
    )

    panel = "\n".join(
        dashboard._strip_ansi(line) for line in dashboard.panel_system()
    )

    assert "待处理检装车 2" in panel
    assert "pending_review 2" in panel
