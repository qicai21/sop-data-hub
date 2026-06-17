"""Functional test for the live service bootstrap script.

Scope:
- scripts/run_live_service.py
- chat_records -> MessageEvent -> RawAssetBundle -> Matcher -> WorkflowTask -> DashboardPayloadQueue -> DashboardState
- runtime/events, runtime/dashboard_intents, runtime/dashboard_state
- R18: PID file, --status, canonical paths, startup validation
- no database, no runtime daemon, no wx-ops-agent writeback
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_chat_record(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")




def test_status_returns_alive_false_when_no_pid(tmp_path):
    """R18: --status returns alive=false when no PID file exists."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    runtime_root = tmp_path / "runtime_nonexistent"

    # Don't create the runtime dir — no PID file should exist
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--status",
            "--runtime-root",
            str(runtime_root),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    status = json.loads(result.stdout)
    assert status["pid"] is None, f"Expected pid=None, got {status['pid']}"
    assert status["alive"] is False, f"Expected alive=False, got {status['alive']}"
    assert "runtime_root" in status
    assert "chat_records_root" in status
    assert "last_log_line" in status


def test_runtime_root_uses_sop_data_hub_semantics():
    """R18: DEFAULT_RUNTIME_ROOT resolves to ~/projects/repos/sop-data-hub/runtime/."""
    from scripts.run_live_service import DEFAULT_RUNTIME_ROOT

    expected = Path.home() / "projects" / "repos" / "sop-data-hub" / "runtime"
    assert DEFAULT_RUNTIME_ROOT == expected, (
        f"DEFAULT_RUNTIME_ROOT should be {expected}, got {DEFAULT_RUNTIME_ROOT}"
    )


def test_default_chat_records_root_points_to_wx_ops_agent():
    """R18: DEFAULT_CHAT_RECORDS_ROOT points to ~/projects/repos/wx-ops-agent/data/chat_records."""
    from scripts.run_live_service import DEFAULT_CHAT_RECORDS_ROOT

    expected = Path.home() / "projects" / "repos" / "wx-ops-agent" / "data" / "chat_records"
    assert DEFAULT_CHAT_RECORDS_ROOT == expected, (
        f"DEFAULT_CHAT_RECORDS_ROOT should be {expected}, got {DEFAULT_CHAT_RECORDS_ROOT}"
    )


# ── R19 regression tests ────────────────────────────────────────────────








def test_source_watcher_default_resolves_correctly():
    """R19: _default_wx_ops_agent_root resolves to ~/projects/repos/wx-ops-agent (parents[4], not .parent)."""
    from sop_hub.sop.source_watcher import _default_wx_ops_agent_root

    root = _default_wx_ops_agent_root()
    # source_watcher.py is at repos/sop-data-hub/src/sop_hub/sop/source_watcher.py
    # parents[4] of that file = repos/
    # So expected = <repo_root>/../../wx-ops-agent
    repo_root = Path(__file__).resolve().parents[2]  # sop-data-hub/
    expected = repo_root.parent / "wx-ops-agent"  # repos/wx-ops-agent
    assert root == expected, f"Expected {expected}, got {root}"


# ── R26: Live SOP Runtime Compiler ────────────────────────────────────


def test_status_includes_sop_runtime(tmp_path):
    """R26: --status includes sop_runtime with loaded_projects, sop_hash, file_hashes, source_of_truth."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    runtime_root = tmp_path / "runtime"

    # R27: Use explicit fixture-dir pointing to config/project_sops/
    fixture_dir = repo_root / "config" / "project_sops"
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--status",
            "--runtime-root",
            str(runtime_root),
            "--fixture-dir",
            str(fixture_dir),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    status = json.loads(result.stdout)
    assert "sop_runtime" in status, f"Expected sop_runtime in status: {status}"
    sr = status["sop_runtime"]
    assert "loaded_projects" in sr
    assert "sop_hash" in sr
    assert "last_reload" in sr
    assert "file_hashes" in sr
    assert "sop_dir" in sr
    # R27: source_of_truth should show git
    assert sr.get("source_of_truth") == "git", f"Expected git, got {sr.get('source_of_truth')}"
    assert len(sr["loaded_projects"]) >= 3, f"Expected >=3 projects, got {sr['loaded_projects']}"
    assert sr["sop_hash"], "sop_hash must not be empty"


def test_sop_watcher_hot_reload_detects_mtime_change(tmp_path):
    """R26: SopWatcher detects mtime changes and returns updated plan."""
    from sop_hub.sop.sop_watcher import SopWatcher
    import time, shutil

    sop_dir = tmp_path / "sops"
    sop_dir.mkdir()

    # R27: Copy YAML fixtures from config/project_sops/
    real_fixture = Path(__file__).resolve().parents[2] / "config" / "project_sops"
    for f in real_fixture.glob("*.yaml"):
        shutil.copy(f, sop_dir / f.name)

    watcher = SopWatcher(sop_dir)
    initial_hash = watcher.runtime.sop_hash
    assert initial_hash, "Initial hash must not be empty"
    assert len(watcher.runtime.loaded_projects) >= 3

    # No change → check_and_reload returns False
    assert watcher.check_and_reload() is False

    # Modify a file
    jljg_sop = sop_dir / "jilin_jingang.yaml"
    original_content = jljg_sop.read_text()
    jljg_sop.write_text(original_content + "\n# WAIT_DELIVERED: 收货确认等待\n")

    # wait for mtime to tick
    time.sleep(0.01)
    # Ensure mtime actually changed (on macOS HFS+, mtime has 1s resolution)
    import os
    os.utime(jljg_sop, None)

    # Now check_and_reload should return True
    assert watcher.check_and_reload() is True, "Should detect mtime change"
    new_hash = watcher.runtime.sop_hash
    assert new_hash != initial_hash, f"Hash should change: {initial_hash} → {new_hash}"
    assert "jilin_jingang_jinzhou" in watcher.runtime.loaded_projects

    # Restore original content
    jljg_sop.write_text(original_content)




def test_sop_watcher_status_reflects_file_hashes(tmp_path):
    """R26: SopRuntime status dict includes per-file hashes."""
    from sop_hub.sop.sop_watcher import SopWatcher
    import shutil

    sop_dir = tmp_path / "sops"
    sop_dir.mkdir()
    # R27: Use YAML fixtures
    real_fixture = Path(__file__).resolve().parents[2] / "config" / "project_sops"
    for f in real_fixture.glob("*.yaml"):
        shutil.copy(f, sop_dir / f.name)

    watcher = SopWatcher(sop_dir)
    status = watcher.status
    assert "file_hashes" in status
    assert "loaded_projects" in status
    assert "sop_hash" in status
    assert "last_reload" in status

    # Each file should have a hash
    file_hashes = status["file_hashes"]
    for f in real_fixture.glob("*.yaml"):
        assert f.name in file_hashes, f"Missing hash for {f.name}"
        assert len(file_hashes[f.name]) == 16, f"Hash should be 16 chars: {file_hashes[f.name]}"
