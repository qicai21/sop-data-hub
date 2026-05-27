#!/usr/bin/env python3
"""Live service bootstrap for SOP Data Hub.

This script keeps the chain local-only:
WxOpsSourceWatcher -> MessageEvent -> RawAssetBundle -> Matcher -> WorkflowTask
-> DashboardPayloadQueue -> DashboardState.

It polls `data/chat_records/**/*.jsonl` continuously, writes event snapshots under
`runtime/events/`, payload JSON under `runtime/dashboard_intents/`, and state JSON
under `runtime/dashboard_state/`.

Canonical repo root: ~/projects/repos/sop-data-hub.
Default runtime root: ~/projects/repos/sop-data-hub/runtime/.
Default chat records root: ~/projects/repos/wx-ops-agent/data/chat_records.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ops_hub.sop.dashboard_payload_queue import build_dashboard_payload_queue, write_dashboard_payload_queue
from ops_hub.sop.dashboard_state_preview import build_dashboard_state_preview, write_dashboard_state_preview
from ops_hub.sop.monitoring_plan_matcher import match_message_event
from ops_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview
from ops_hub.sop.sop_watcher import SopWatcher
from ops_hub.sop.source_watcher import WxOpsSourceWatcher
from ops_hub.sop.workflow_task import build_workflow_task_queue

# ── R18: canonical paths ────────────────────────────────────────────────
CANONICAL_REPO_ROOT = Path.home() / "projects" / "repos" / "sop-data-hub"
DEFAULT_RUNTIME_ROOT = CANONICAL_REPO_ROOT / "runtime"
DEFAULT_CHAT_RECORDS_ROOT = Path.home() / "projects" / "repos" / "wx-ops-agent" / "data" / "chat_records"
# ── R27: canonical SOP source → config/project_sops/ (YAML, git-tracked) ──
DEFAULT_FIXTURE_DIR = CANONICAL_REPO_ROOT / "config" / "project_sops"
DEFAULT_POLL_INTERVAL = 1.0
PID_FILE_NAME = "live_service.pid"


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_component(value: str) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
    return cleaned or "unknown"


def _event_source_file_stem(event) -> str:
    source_file = str(event.metadata.get("source_file") or "").strip()
    if source_file:
        return Path(source_file).stem
    return _safe_component(str(event.metadata.get("source_file_stem") or "unknown"))


def _event_to_dict(event) -> dict[str, Any]:
    return {
        "message_id": event.message_id,
        "channel": event.channel,
        "group_id": event.group_id,
        "source_agent": event.source_agent,
        "received_at": event.received_at,
        "message_type": event.message_type,
        "text": event.text,
        "raw_asset_bundle": event.raw_asset_bundle.to_dict() if event.raw_asset_bundle else None,
        "metadata": dict(event.metadata),
    }


def _write_event_snapshot(event, *, runtime_root: Path) -> Path:
    group_name = _safe_component(str(event.metadata.get("group_name") or event.group_id or "unknown"))
    source_stem = _safe_component(_event_source_file_stem(event))
    event_dir = runtime_root / "events" / group_name / source_stem
    event_dir.mkdir(parents=True, exist_ok=True)
    event_path = event_dir / f"{event.message_id}.json"
    event_path.write_text(json.dumps(_event_to_dict(event), ensure_ascii=False, indent=2), encoding="utf-8")
    return event_path


def _configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ops_hub.live_service")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


def _seen_key(event) -> tuple[str, str]:
    source_file = str(event.metadata.get("source_file") or "")
    local_id = str(event.metadata.get("local_id") or event.message_id)
    return source_file, local_id


def _load_monitoring_plan(fixture_dir: Path) -> dict[str, Any]:
    return build_real_sop_monitoring_plan_preview(fixture_dir).plan


_SOP_WATCHER_CACHE: dict[str, SopWatcher] = {}


def _get_sop_watcher(fixture_dir: Path) -> SopWatcher:
    """Get or create a SopWatcher keyed by the fixture directory."""
    key = str(fixture_dir.resolve())
    if key not in _SOP_WATCHER_CACHE:
        _SOP_WATCHER_CACHE[key] = SopWatcher(fixture_dir)
    return _SOP_WATCHER_CACHE[key]


def _write_pid_file(runtime_root: Path, pid: int) -> Path:
    """Write the current process PID to runtime/live_service.pid."""
    pid_path = runtime_root / PID_FILE_NAME
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(pid), encoding="utf-8")
    return pid_path


def _read_pid_file(runtime_root: Path) -> int | None:
    """Read PID from runtime/live_service.pid; return None if missing or unparseable."""
    pid_path = runtime_root / PID_FILE_NAME
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_last_log_line(log_path: Path) -> str:
    """Return the last non-empty line of the log file, or empty string."""
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        return lines[-1] if lines else ""
    except OSError:
        return ""


def _validate_startup(
    *,
    pid: int,
    runtime_root: Path,
    chat_records_root: Path,
    logger: logging.Logger,
) -> None:
    """R18: validate startup preconditions."""
    # confirm current PID
    logger.info("startup validation pid=%s", pid)

    # confirm service is alive (we are the process)
    if not _is_process_alive(pid):
        raise RuntimeError(f"Process PID {pid} is not alive during startup validation")

    # confirm chat_records_root exists
    if not chat_records_root.exists():
        raise FileNotFoundError(f"chat_records_root does not exist: {chat_records_root}")
    if not chat_records_root.is_dir():
        raise NotADirectoryError(f"chat_records_root is not a directory: {chat_records_root}")

    # confirm runtime subdirs are created
    required_dirs = [
        runtime_root / "events",
        runtime_root / "dashboard_intents",
        runtime_root / "dashboard_state",
    ]
    for d in required_dirs:
        d.mkdir(parents=True, exist_ok=True)
        if not d.is_dir():
            raise NotADirectoryError(f"Failed to create runtime directory: {d}")

    logger.info("startup validation passed runtime_root=%s chat_records_root=%s", runtime_root, chat_records_root)


def process_event_once(*, event, monitoring_plan: dict[str, Any], runtime_root: Path, logger: logging.Logger) -> dict[str, Any]:
    event_path = _write_event_snapshot(event, runtime_root=runtime_root)
    match_result = match_message_event(event, monitoring_plan)
    workflow_queue = build_workflow_task_queue(event, event.raw_asset_bundle, match_result)

    payload_queue = build_dashboard_payload_queue(
        workflow_queue,
        created_at=event.received_at or _utc_now_iso(),
        output_dir=runtime_root / "dashboard_intents",
    )
    payload_paths = write_dashboard_payload_queue(payload_queue)

    preview = build_dashboard_state_preview(
        payload_queue,
        updated_at=event.received_at or _utc_now_iso(),
        output_dir=runtime_root / "dashboard_state",
    )
    state_paths = write_dashboard_state_preview(preview)

    logger.info(
        "processed event message_id=%s group_id=%s payloads=%s states=%s event_path=%s",
        event.message_id,
        event.group_id,
        len(payload_paths),
        len(state_paths),
        event_path,
    )

    return {
        "event_path": event_path,
        "payload_paths": payload_paths,
        "state_paths": state_paths,
        "workflow_tasks": len(workflow_queue.workflow_tasks),
        "todo_items": len(workflow_queue.todo_items),
        "payloads_written": len(payload_paths),
        "states_written": len(state_paths),
    }


def run_once(
    *,
    chat_records_root: Path,
    runtime_root: Path,
    fixture_dir: Path,
    logger: logging.Logger,
    seen: set[tuple[str, str]] | None = None,
    sync_start_id: int | None = None,
    sop_watcher: SopWatcher | None = None,
) -> int:
    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)
    sop_watcher = sop_watcher or _get_sop_watcher(fixture_dir)
    # Check for SOP changes before this poll
    sop_changed = sop_watcher.check_and_reload()
    if sop_changed:
        logger.info("sop watcher: plan updated hash=%s projects=%s", sop_watcher.runtime.sop_hash, sop_watcher.runtime.loaded_projects)
    monitoring_plan = sop_watcher.monitoring_plan
    seen = seen if seen is not None else set()
    processed = 0

    for event in watcher.iter_message_events():
        local_id = event.metadata.get("local_id")
        if sync_start_id is not None and isinstance(local_id, int) and local_id < sync_start_id:
            continue
        key = _seen_key(event)
        if key in seen:
            continue
        seen.add(key)
        process_event_once(event=event, monitoring_plan=monitoring_plan, runtime_root=runtime_root, logger=logger)
        processed += 1

    return processed


def run_live_service(
    *,
    chat_records_root: Path,
    runtime_root: Path,
    fixture_dir: Path,
    poll_interval: float,
    once: bool,
    max_iterations: int | None = None,
    sync_start_id: int | None = None,
) -> None:
    log_path = runtime_root / "live_service.log"
    logger = _configure_logging(log_path)

    # ── R18: write PID file ─────────────────────────────────────────────
    pid = os.getpid()
    pid_path = _write_pid_file(runtime_root, pid)
    logger.info("pid=%s pid_file=%s", pid, pid_path)

    # ── R18: startup validation ─────────────────────────────────────────
    _validate_startup(
        pid=pid,
        runtime_root=runtime_root,
        chat_records_root=chat_records_root,
        logger=logger,
    )

    logger.info("live service starting")
    logger.info("chat_records_root=%s", chat_records_root)
    logger.info("runtime_root=%s", runtime_root)
    logger.info("event_output=%s", runtime_root / "events")
    logger.info("dashboard_intents_output=%s", runtime_root / "dashboard_intents")
    logger.info("dashboard_state_output=%s", runtime_root / "dashboard_state")
    logger.info("log_path=%s", log_path)
    logger.info("fixture_dir=%s", fixture_dir)

    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "events").mkdir(parents=True, exist_ok=True)
    (runtime_root / "dashboard_intents").mkdir(parents=True, exist_ok=True)
    (runtime_root / "dashboard_state").mkdir(parents=True, exist_ok=True)

    # ── R26: initialize SOP watcher ──────────────────────────────────────
    sop_watcher = _get_sop_watcher(fixture_dir)
    logger.info("sop watcher: initialized hash=%s projects=%s", sop_watcher.runtime.sop_hash, sop_watcher.runtime.loaded_projects)

    seen: set[tuple[str, str]] = set()
    iterations = 0

    while True:
        iterations += 1
        processed = run_once(
            chat_records_root=chat_records_root,
            runtime_root=runtime_root,
            fixture_dir=fixture_dir,
            logger=logger,
            seen=seen,
            sync_start_id=sync_start_id,
            sop_watcher=sop_watcher,
        )
        logger.info("poll complete processed=%s seen=%s", processed, len(seen))
        if once:
            return
        if max_iterations is not None and iterations >= max_iterations:
            return
        time.sleep(max(poll_interval, 0.1))


# ── R18: --status command ───────────────────────────────────────────────
def status_command(runtime_root: Path, fixture_dir: Path | None = None) -> None:
    """Print live service status as JSON to stdout."""
    pid = _read_pid_file(runtime_root)
    alive = _is_process_alive(pid) if pid is not None else False
    log_path = runtime_root / "live_service.log"
    last_log_line = _read_last_log_line(log_path)
    chat_records_root = str(DEFAULT_CHAT_RECORDS_ROOT) if DEFAULT_CHAT_RECORDS_ROOT.exists() else str(DEFAULT_CHAT_RECORDS_ROOT)

    status = {
        "pid": pid,
        "alive": alive,
        "runtime_root": str(runtime_root.resolve()),
        "chat_records_root": chat_records_root,
        "last_log_line": last_log_line,
    }

    # ── R26: sop_runtime section ────────────────────────────────────
    if fixture_dir:
        try:
            sop_watcher = _get_sop_watcher(fixture_dir)
            sop_watcher.check_and_reload()
            status["sop_runtime"] = sop_watcher.status
        except Exception as exc:
            status["sop_runtime"] = {"error": str(exc)}

    print(json.dumps(status, ensure_ascii=False, indent=2))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SOP Data Hub live service.")
    parser.add_argument(
        "--chat-records-root",
        type=Path,
        default=None,
        help="Path to wx-ops-agent data/chat_records root (default: ~/projects/repos/wx-ops-agent/data/chat_records)",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Output root for runtime/events, runtime/dashboard_intents, runtime/dashboard_state (default: ~/projects/repos/sop-data-hub/runtime)",
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
        help="SOP fixture directory used to build the monitoring plan",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll and exit (test/bootstrap mode)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop after N polling iterations",
    )
    parser.add_argument(
        "--sync-start-id",
        type=int,
        default=None,
        help="Minimum local_id/seq to process; skip events with lower IDs (R19: catch-up after path fix)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print live service status (pid, alive, runtime_root, chat_records_root, last_log_line) and exit",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if args.status:
        status_command(runtime_root=args.runtime_root, fixture_dir=args.fixture_dir)
        return

    # Resolve chat_records_root: explicit arg > env var > canonical default
    if args.chat_records_root:
        chat_records_root = args.chat_records_root
    else:
        watcher = WxOpsSourceWatcher()
        # If watcher resolved to the default (parent-chain-derived path), use our canonical default instead
        chat_records_root = DEFAULT_CHAT_RECORDS_ROOT

    run_live_service(
        chat_records_root=chat_records_root,
        runtime_root=args.runtime_root,
        fixture_dir=args.fixture_dir,
        poll_interval=args.poll_interval,
        once=args.once,
        max_iterations=args.max_iterations,
        sync_start_id=args.sync_start_id,
    )


if __name__ == "__main__":
    main()
