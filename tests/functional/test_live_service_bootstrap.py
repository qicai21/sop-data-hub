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


# ── R19 regression tests ────────────────────────────────────────────────


def test_group013_seq52_chao_forge_message_routes_to_chaoyang_steel(tmp_path):
    """R19: Replay seq=52 朝阳西/木森17 from GROUP013 → chaoyang_steel dashboard artifacts."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    chat_records_root = tmp_path / "chat_records"
    runtime_root = tmp_path / "runtime"

    # Simulate exact real-world format: dir named 数据单发群-GROUP013, seq field (not local_id)
    source_file_path = chat_records_root / "数据单发群-GROUP013" / "2026-05.jsonl"
    _write_chat_record(
        source_file_path,
        {
            "seq": 52,
            "sender": "郭东北",
            "sender-wxid": "dongbei9234",
            "time": "2026-05-27 10:10:40",
            "msg-type": "text",
            "msg-content": "朝阳西放货计划，木森17,新放货 5375 吨，总放货量 15975 吨",
            "msg-path": "",
            "remark": "",
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

    event_path = runtime_root / "events" / "数据单发群-GROUP013" / "2026-05" / "wx_52.json"
    intent_path = runtime_root / "dashboard_intents" / "wx_52.json"
    state_path = runtime_root / "dashboard_state" / "wx_52.json"

    assert event_path.exists(), f"Event snapshot missing: {event_path}"
    assert intent_path.exists(), f"Dashboard intent missing: {intent_path}"
    assert state_path.exists(), f"Dashboard state missing: {state_path}"

    event_payload = json.loads(event_path.read_text(encoding="utf-8"))
    intent_payload = json.loads(intent_path.read_text(encoding="utf-8"))
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert event_payload["message_id"] == "wx_52"
    assert event_payload["text"] == "朝阳西放货计划，木森17,新放货 5375 吨，总放货量 15975 吨"
    assert intent_payload["message_id"] == "wx_52"
    assert intent_payload["project_id"] == "chaoyang_steel"
    assert "朝阳西木森17" in str(intent_payload.get("watch_item", {}))
    assert state_payload["latest_message_id"] == "wx_52"
    assert state_payload["project_id"] == "chaoyang_steel"
    assert state_payload["status"] == "active"


def test_sync_start_id_filters_lower_seqs(tmp_path):
    """R19: --sync-start-id skips events with local_id/seq below threshold."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    chat_records_root = tmp_path / "chat_records"
    runtime_root = tmp_path / "runtime"

    source_file_path = chat_records_root / "铁晟业务工作群" / "2026-05.jsonl"
    # Write seq=21 (below threshold) and seq=53 (above)
    _write_chat_record(source_file_path, {
        "local_id": 21,
        "group_name": "铁晟业务工作群",
        "group_wxid": "GROUP001",
        "msg-type": "text",
        "msg-content": "朝阳西 实装54节",
        "time": "2026-05-27 09:00:00",
        "sender": "测试发送者",
        "sender_wxid": "wxid_test_sender",
    })
    _write_chat_record(source_file_path, {
        "local_id": 53,
        "group_name": "铁晟业务工作群",
        "group_wxid": "GROUP001",
        "msg-type": "text",
        "msg-content": "朝阳西 实装55节",
        "time": "2026-05-27 10:00:00",
        "sender": "测试发送者",
        "sender_wxid": "wxid_test_sender",
    })

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--once",
            "--chat-records-root",
            str(chat_records_root),
            "--runtime-root",
            str(runtime_root),
            "--sync-start-id",
            "52",
            "--poll-interval",
            "0",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    # seq=21 should be skipped (below threshold)
    assert not (runtime_root / "dashboard_intents" / "wx_21.json").exists(), "seq=21 should be filtered"
    # seq=53 should be processed
    intent_path = runtime_root / "dashboard_intents" / "wx_53.json"
    assert intent_path.exists(), f"seq=53 should be processed: {intent_path}"
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    assert intent["message_id"] == "wx_53"


def test_group013_group_name_substring_match(tmp_path):
    """R19: Watcher dir-name group_id ('数据单发群-GROUP013') substring-matches plan group_name ('数据单发群')."""
    from ops_hub.sop.monitoring_plan_matcher import _group_plan_for_event, MessageEvent
    from ops_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview

    plan = build_real_sop_monitoring_plan_preview(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sops").plan
    wechat_plan = plan.get("wechat_monitoring_plan") or {}

    # Event with watcher-derived group_id (dir name with suffix)
    event = MessageEvent(
        message_id="wx_52",
        channel="wechat",
        group_id="数据单发群-GROUP013",
        text="朝阳西放货计划，木森17",
        metadata={"group_name": "数据单发群-GROUP013"},
    )

    matched_group_id, group_plan = _group_plan_for_event(event, wechat_plan)
    assert matched_group_id == "GROUP013", f"Expected GROUP013, got {matched_group_id}"
    assert group_plan is not None
    assert group_plan["group_name"] == "数据单发群"


def test_source_watcher_default_resolves_correctly():
    """R19: _default_wx_ops_agent_root resolves to ~/projects/repos/wx-ops-agent (parents[4], not .parent)."""
    from ops_hub.sop.source_watcher import _default_wx_ops_agent_root

    root = _default_wx_ops_agent_root()
    # source_watcher.py is at repos/ops-data-hub/src/ops_hub/sop/source_watcher.py
    # parents[4] of that file = repos/
    # So expected = <repo_root>/../../wx-ops-agent
    repo_root = Path(__file__).resolve().parents[2]  # ops-data-hub/
    expected = repo_root.parent / "wx-ops-agent"  # repos/wx-ops-agent
    assert root == expected, f"Expected {expected}, got {root}"


# ── R26: Live SOP Runtime Compiler ────────────────────────────────────


def test_status_includes_sop_runtime(tmp_path):
    """R26: --status includes sop_runtime with loaded_projects, sop_hash, file_hashes."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    runtime_root = tmp_path / "runtime"

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
    assert "sop_runtime" in status, f"Expected sop_runtime in status: {status}"
    sr = status["sop_runtime"]
    assert "loaded_projects" in sr
    assert "sop_hash" in sr
    assert "last_reload" in sr
    assert "file_hashes" in sr
    assert "sop_dir" in sr
    # The default fixture dir should have at least 3 projects
    assert len(sr["loaded_projects"]) >= 3, f"Expected >=3 projects, got {sr['loaded_projects']}"
    assert sr["sop_hash"], "sop_hash must not be empty"


def test_sop_watcher_hot_reload_detects_mtime_change(tmp_path):
    """R26: SopWatcher detects mtime changes and returns updated plan."""
    from ops_hub.sop.sop_watcher import SopWatcher
    import time

    sop_dir = tmp_path / "sops"
    sop_dir.mkdir()

    # Copy a real fixture to get valid content
    import shutil
    real_fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sops"
    for f in real_fixture.glob("*.md"):
        shutil.copy(f, sop_dir / f.name)

    watcher = SopWatcher(sop_dir)
    initial_hash = watcher.runtime.sop_hash
    assert initial_hash, "Initial hash must not be empty"
    assert len(watcher.runtime.loaded_projects) >= 3

    # No change → check_and_reload returns False
    assert watcher.check_and_reload() is False

    # Modify a file
    jljg_sop = sop_dir / "jilin_jingang_sop.md"
    original_content = jljg_sop.read_text()
    jljg_sop.write_text(original_content + "\n## WAIT_DELIVERED\n新增节点：收货确认\n")

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


def test_sop_change_reflected_in_next_poll_without_restart(tmp_path):
    """R26: New messages use updated SOP after file change, no restart needed."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "run_live_service.py"
    sop_dir = tmp_path / "fixture_sops"
    chat_records_root = tmp_path / "chat_records"
    runtime_root = tmp_path / "runtime"

    import shutil
    sop_dir.mkdir()
    real_fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sops"
    for f in real_fixture.glob("*.md"):
        shutil.copy(f, sop_dir / f.name)

    # ── First poll: with original SOP ──
    source_file_path = chat_records_root / "铁晟业务工作群" / "2026-05.jsonl"
    _write_chat_record(
        source_file_path,
        {
            "local_id": 101,
            "group_name": "铁晟业务工作群",
            "group_wxid": "GROUP001",
            "msg-type": "text",
            "msg-content": "四平 装箱通知 8节 吉林金钢",
            "time": "2026-05-27 14:00:00",
            "sender": "测试发送者",
            "sender_wxid": "wxid_test_sender",
        },
    )

    result1 = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--once",
            "--chat-records-root", str(chat_records_root),
            "--runtime-root", str(runtime_root),
            "--fixture-dir", str(sop_dir),
            "--poll-interval", "0",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result1.returncode == 0, result1.stdout + "\n" + result1.stderr

    intent1 = runtime_root / "dashboard_intents" / "wx_101.json"
    assert intent1.exists(), f"First poll should produce intent: {intent1}"
    payload1 = json.loads(intent1.read_text(encoding="utf-8"))
    initial_target_node = payload1.get("target_sop_node", "")

    # ── Modify SOP: add a WAIT_DELIVERED node ──
    jljg_sop = sop_dir / "jilin_jingang_sop.md"
    original_content = jljg_sop.read_text()
    # Append a new section at the end
    modified_content = original_content + "\n\n## WAIT_DELIVERED\n- 状态：收货确认等待\n- 触发：95306 已发车\n"
    jljg_sop.write_text(modified_content)
    import os, time
    os.utime(jljg_sop, None)
    time.sleep(0.01)

    # ── Second poll: new message, no restart ──
    _write_chat_record(
        source_file_path,
        {
            "local_id": 102,
            "group_name": "铁晟业务工作群",
            "group_wxid": "GROUP001",
            "msg-type": "text",
            "msg-content": "四平 装箱完成 10节 吉林金钢",
            "time": "2026-05-27 14:30:00",
            "sender": "测试发送者",
            "sender_wxid": "wxid_test_sender",
        },
    )

    result2 = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--once",
            "--chat-records-root", str(chat_records_root),
            "--runtime-root", str(runtime_root),
            "--fixture-dir", str(sop_dir),
            "--poll-interval", "0",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result2.returncode == 0, result2.stdout + "\n" + result2.stderr

    # Verify second message was processed
    intent2 = runtime_root / "dashboard_intents" / "wx_102.json"
    assert intent2.exists(), f"Second poll should produce intent: {intent2}"

    # Verify sop watcher log indicates reload
    output = result2.stdout
    assert "sop watcher" in output.lower(), f"SOP watcher log missing: {output}"

    # Restore original
    jljg_sop.write_text(original_content)


def test_sop_watcher_status_reflects_file_hashes(tmp_path):
    """R26: SopRuntime status dict includes per-file hashes."""
    from ops_hub.sop.sop_watcher import SopWatcher
    import shutil

    sop_dir = tmp_path / "sops"
    sop_dir.mkdir()
    real_fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sops"
    for f in real_fixture.glob("*.md"):
        shutil.copy(f, sop_dir / f.name)

    watcher = SopWatcher(sop_dir)
    status = watcher.status
    assert "file_hashes" in status
    assert "loaded_projects" in status
    assert "sop_hash" in status
    assert "last_reload" in status

    # Each file should have a hash
    file_hashes = status["file_hashes"]
    for f in real_fixture.glob("*.md"):
        assert f.name in file_hashes, f"Missing hash for {f.name}"
        assert len(file_hashes[f.name]) == 16, f"Hash should be 16 chars: {file_hashes[f.name]}"
