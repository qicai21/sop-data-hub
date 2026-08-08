"""Portable path resolution for scripts/run_live_service.py (Phase 0b)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_rls():
    scripts_dir = str(REPO_ROOT)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Prefer package-style import used elsewhere in the suite.
    if "scripts.run_live_service" in sys.modules:
        return importlib.reload(sys.modules["scripts.run_live_service"])
    return importlib.import_module("scripts.run_live_service")


def test_cli_overrides_win_env_and_defaults(tmp_path: Path):
    rls = _load_rls()
    cli_runtime = tmp_path / "cli-runtime"
    cli_chat = tmp_path / "cli-chat"
    cli_fix = tmp_path / "cli-fix"
    env = {
        rls.ENV_RUNTIME_ROOT: str(tmp_path / "env-runtime"),
        rls.ENV_CHAT_RECORDS_ROOT: str(tmp_path / "env-chat"),
        rls.ENV_FIXTURE_DIR: str(tmp_path / "env-fix"),
    }
    paths = rls.resolve_live_service_paths(
        runtime_root=cli_runtime,
        chat_records_root=cli_chat,
        fixture_dir=cli_fix,
        env=env,
    )
    assert paths["runtime_root"] == cli_runtime
    assert paths["chat_records_root"] == cli_chat
    assert paths["fixture_dir"] == cli_fix


def test_env_overrides_defaults(tmp_path: Path):
    rls = _load_rls()
    env_runtime = tmp_path / "env-runtime"
    env_chat = tmp_path / "env-chat"
    env_fix = tmp_path / "env-fix"
    paths = rls.resolve_live_service_paths(
        runtime_root=None,
        chat_records_root=None,
        fixture_dir=None,
        env={
            rls.ENV_RUNTIME_ROOT: str(env_runtime),
            rls.ENV_CHAT_RECORDS_ROOT: str(env_chat),
            rls.ENV_FIXTURE_DIR: str(env_fix),
        },
    )
    assert paths["runtime_root"] == env_runtime
    assert paths["chat_records_root"] == env_chat
    assert paths["fixture_dir"] == env_fix


def test_default_fixture_prefers_this_repo_when_present():
    rls = _load_rls()
    paths = rls.resolve_live_service_paths(
        runtime_root=None,
        chat_records_root=None,
        fixture_dir=None,
        env={},
    )
    # This checkout has config/project_sops — portable hosts should use it.
    expected = REPO_ROOT / "config" / "project_sops"
    assert expected.is_dir()
    assert paths["fixture_dir"] == expected


def test_status_honors_runtime_root_cli(tmp_path: Path):
    """--status with --runtime-root must not require home layout."""
    import json
    import subprocess
    import sys

    runtime_root = tmp_path / "runtime_status"
    script = REPO_ROOT / "scripts" / "run_live_service.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--status",
            "--runtime-root",
            str(runtime_root),
            "--fixture-dir",
            str(REPO_ROOT / "config" / "project_sops"),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    status = json.loads(result.stdout)
    assert Path(status["runtime_root"]) == runtime_root
