from __future__ import annotations

from types import SimpleNamespace

from sop_hub.sop import text_watch_daemon


def test_pending_candidate_verifier_runs_only_when_enabled_and_due(monkeypatch):
    calls: list[str] = []

    def fake_verify_pending_candidates(*, db_path):
        calls.append(str(db_path))
        return SimpleNamespace(
            to_dict=lambda: {
                "scanned": 1,
                "retried": 1,
                "succeeded": 0,
                "still_pending": 1,
            }
        )

    monkeypatch.setattr(
        "sop_hub.sop.pending_match_verifier.verify_pending_candidates",
        fake_verify_pending_candidates,
    )
    monkeypatch.setattr(
        text_watch_daemon,
        "_last_pending_candidate_verify_at",
        0.0,
    )

    assert (
        text_watch_daemon._run_pending_candidate_verifier_if_due(
            "agent.db",
            enabled=False,
            now_monotonic=100.0,
        )
        is None
    )
    first = text_watch_daemon._run_pending_candidate_verifier_if_due(
        "agent.db",
        enabled=True,
        now_monotonic=100.0,
    )
    assert first == {
        "scanned": 1,
        "retried": 1,
        "succeeded": 0,
        "still_pending": 1,
    }
    assert calls == ["agent.db"]

    assert (
        text_watch_daemon._run_pending_candidate_verifier_if_due(
            "agent.db",
            enabled=True,
            now_monotonic=120.0,
        )
        is None
    )
    assert calls == ["agent.db"]
