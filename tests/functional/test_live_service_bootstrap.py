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


def test_run_live_service_bootstrap_once_writes_runtime_artifacts_and_pid_file(tmp_path):
    """R18: --once works and writes runtime artifacts + PID file."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    chat_records_root = tmp_path / "chat_records"
    runtime_root = tmp_path / "runtime"

    source_file_path = chat_records_root / "铁晟业务工作群" / "2026-04.jsonl"
    _write_chat_record(
        source_file_path,
        {
            "local_id": 21,
            "group_name": "铁晟业务工作群",
            "group_wxid": "GROUP001",
            "msg-type": "text",
            "msg-content": "朝阳西 实装54节",
            "time": "2026-05-26 09:00:00",
            "server_id": 900021,
            "message_key": "msg-21",
            "sender": "测试发送者",
            "sender_wxid": "wxid_test_sender",
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--once",
            "--chat-records-root",
            str(chat_records_root),
            "--runtime-root",
            str(runtime_root),
            "--poll-interval",
            "0",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    event_path = runtime_root / "events" / "铁晟业务工作群" / "2026-04" / "wx_21.json"
    intent_path = runtime_root / "dashboard_intents" / "wx_21.json"
    state_path = runtime_root / "dashboard_state" / "wx_21.json"
    pid_path = runtime_root / "live_service.pid"

    assert event_path.exists()
    assert intent_path.exists()
    assert state_path.exists()

    # R18: PID file must exist and contain a valid PID
    assert pid_path.exists(), "PID file must be written after startup"
    pid_text = pid_path.read_text(encoding="utf-8").strip()
    assert pid_text.isdigit(), f"PID file must contain a number, got: {pid_text}"

    event_payload = json.loads(event_path.read_text(encoding="utf-8"))
    intent_payload = json.loads(intent_path.read_text(encoding="utf-8"))
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert event_payload["message_id"] == "wx_21"
    assert event_payload["metadata"]["local_id"] == 21
    assert intent_payload["message_id"] == "wx_21"
    assert intent_payload["project_id"] == "chaoyang_steel"
    assert state_payload["latest_message_id"] == "wx_21"
    assert state_payload["project_id"] == "chaoyang_steel"
    assert state_payload["status"] == "active"


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
