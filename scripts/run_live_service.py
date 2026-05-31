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
from ops_hub.sop.executor_runner import run_departure_executor_chain_if_applicable
from ops_hub.sop.sop_watcher import SopWatcher
from ops_hub.sop.source_watcher import WxOpsSourceWatcher
from ops_hub.sop.workflow_task import build_workflow_task_queue

# ── R52: executor runner ─────────────────────────────────────────────
from ops_hub.sop.executor_runner import run_departure_executor_chain_if_applicable

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


def _write_status_state(runtime_root: Path, *, message_id: str, processed_at: str) -> Path:
    """Write last-processed tracking to runtime/live_service_state.json."""
    state_path = runtime_root / "live_service_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_processed_message_id": str(message_id),
        "last_processed_time": processed_at,
        "alive": True,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state_path


def _read_status_state(runtime_root: Path) -> dict[str, str]:
    """Read last-processed state from runtime/live_service_state.json."""
    state_path = runtime_root / "live_service_state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

# ── R58: persistent cursor ─────────────────────────────────────────────
CURSOR_FILE_NAME = "live_service_cursor.json"
CURSOR_VERSION = 1


def _cursor_path(runtime_root: Path, cursor_path_override: Path | None = None) -> Path:
    if cursor_path_override:
        return cursor_path_override
    return runtime_root / "cursors" / CURSOR_FILE_NAME


def _load_cursor(runtime_root: Path, cursor_path_override: Path | None = None) -> dict[str, Any]:
    path = _cursor_path(runtime_root, cursor_path_override)
    if not path.exists():
        return {"version": CURSOR_VERSION, "sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "sources" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"version": CURSOR_VERSION, "sources": {}}


def _save_cursor(runtime_root: Path, cursor: dict[str, Any], cursor_path_override: Path | None = None) -> Path:
    path = _cursor_path(runtime_root, cursor_path_override)
    path.parent.mkdir(parents=True, exist_ok=True)
    cursor["updated_at"] = _utc_now_iso()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(cursor, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return path


def _bootstrap_cursor_to_latest(runtime_root: Path, chat_records_root: Path, cursor_path_override: Path | None = None) -> dict[str, Any]:
    """Scan all chat records, find the max local_id per source, and build a cursor at the latest position."""
    from collections import defaultdict
    max_ids: dict[str, dict[str, Any]] = defaultdict(lambda: {"last_local_id": 0})

    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)
    for event in watcher.iter_message_events():
        source = str(event.metadata.get("source_file") or "")
        local_id = event.metadata.get("local_id")
        if not source or local_id is None:
            continue
        lid = int(local_id) if not isinstance(local_id, int) else local_id
        if lid > max_ids[source].get("last_local_id", 0):
            max_ids[source] = {
                "last_local_id": lid,
                "last_message_id": event.message_id,
                "last_processed_at": _utc_now_iso(),
                "group_name": str(event.metadata.get("group_name") or ""),
            }

    cursor = {
        "version": CURSOR_VERSION,
        "bootstrap_cursor": True,
        "sources": dict(max_ids),
    }
    _save_cursor(runtime_root, cursor, cursor_path_override)
    return cursor


def _update_cursor_for_event(runtime_root: Path, cursor: dict[str, Any], event, cursor_path_override: Path | None = None) -> None:
    """Update cursor after processing an event. Saves atomically."""
    source = str(event.metadata.get("source_file") or "")
    local_id = event.metadata.get("local_id")
    if not source or local_id is None:
        return
    lid = int(local_id) if not isinstance(local_id, int) else local_id
    sources = cursor.setdefault("sources", {})
    current = sources.get(source, {})
    if lid > current.get("last_local_id", 0):
        sources[source] = {
            "last_local_id": lid,
            "last_message_id": event.message_id,
            "last_processed_at": _utc_now_iso(),
            "group_name": str(event.metadata.get("group_name") or ""),
        }
        _save_cursor(runtime_root, cursor, cursor_path_override)


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


# ── R59.1: waiting_media index (retry for images whose download completes later) ──

def _waiting_media_index_path(runtime_root: Path) -> Path:
    return runtime_root / "waiting_media_index.json"


def _load_waiting_media_index(runtime_root: Path) -> dict[str, Any]:
    path = _waiting_media_index_path(runtime_root)
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "items": {}}


def _save_waiting_media_index(runtime_root: Path, index: dict[str, Any]) -> Path:
    path = _waiting_media_index_path(runtime_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def _add_to_waiting_media_index(runtime_root: Path, event: Any, logger: logging.Logger) -> None:
    """Record a waiting_media event so it can be retried when the image becomes available."""
    index = _load_waiting_media_index(runtime_root)
    mid = event.message_id
    if mid in index.get("items", {}):
        entry = index["items"][mid]
        entry["retries"] = entry.get("retries", 0) + 1
        entry["last_checked_at"] = _utc_now_iso()
    else:
        index["items"][mid] = {
            "message_id": mid,
            "source_file": event.metadata.get("source_file", ""),
            "local_id": event.metadata.get("local_id"),
            "group_name": event.metadata.get("group_name", ""),
            "image_md5": event.metadata.get("image_md5", ""),
            "msg_path": event.metadata.get("msg_path", ""),
            "media_status": event.metadata.get("media_status", ""),
            "added_at": _utc_now_iso(),
            "retries": 0,
            "status": "waiting",
        }
        logger.info("waiting_media_index: added message_id=%s", mid)
    index["items"][mid]["last_checked_at"] = _utc_now_iso()
    _save_waiting_media_index(runtime_root, index)


def _mark_waiting_media_done(runtime_root: Path, message_id: str, logger: logging.Logger, index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark a waiting_media entry as done in-place, or load/save if no index provided."""
    if index is None:
        index = _load_waiting_media_index(runtime_root)
    if message_id in index.get("items", {}):
        index["items"][message_id]["status"] = "done"
        index["items"][message_id]["resolved_at"] = _utc_now_iso()
        _save_waiting_media_index(runtime_root, index)
        logger.info("waiting_media_index: marked done message_id=%s", message_id)
    return index


def _retry_waiting_media(
    *,
    runtime_root: Path,
    chat_records_root: Path,
    monitoring_plan: dict[str, Any],
    logger: logging.Logger,
    apply_mode: bool = False,
    cursor: dict[str, Any] | None = None,
    cursor_path_override: Path | None = None,
) -> int:
    """Check all waiting_media index entries; re-process those whose images have become available."""
    from ops_hub.sop.source_watcher import WxOpsSourceWatcher

    index = _load_waiting_media_index(runtime_root)
    items = index.get("items", {})
    retried = 0
    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)

    for mid, entry in list(items.items()):
        if entry.get("status") == "done":
            continue

        source_file = entry.get("source_file", "")
        local_id = entry.get("local_id")
        if not source_file:
            logger.warning("waiting_media_retry: no source_file for message_id=%s, marking done", mid)
            _mark_waiting_media_done(runtime_root, mid, logger)
            continue

        # Re-read the payload from the source JSONL
        src_path = Path(source_file)
        payload = None
        if src_path.exists():
            try:
                with src_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            p = json.loads(line)
                        except Exception:
                            continue
                        lid = p.get("local_id") or p.get("seq")
                        if lid is not None and int(lid) == local_id:
                            payload = p
                            break
            except Exception:
                pass

        if payload is None:
            # Payload not found — skip, keep in index for now
            entry["retries"] = entry.get("retries", 0) + 1
            entry["last_checked_at"] = _utc_now_iso()
            continue

        # Re-build event through source_watcher (re-evaluates image path)
        new_event = watcher._build_event(source_path=src_path, payload=payload)
        new_ms = new_event.metadata.get("media_status", "")
        new_ps = new_event.metadata.get("processing_status", "")

        if new_ps != "waiting_media":
            # Media is now available — process it
            logger.info(
                "waiting_media_retry: media ready message_id=%s media_status=%s -> %s",
                mid, entry.get("media_status"), new_ms,
            )
            process_event_once(
                event=new_event,
                monitoring_plan=monitoring_plan,
                runtime_root=runtime_root,
                logger=logger,
                apply_mode=apply_mode,
                cursor=None,  # do NOT regress cursor
                cursor_path_override=cursor_path_override,
            )
            index = _mark_waiting_media_done(runtime_root, mid, logger, index=index)
            retried += 1
        else:
            entry["retries"] = entry.get("retries", 0) + 1
            entry["last_checked_at"] = _utc_now_iso()
            entry["media_status"] = new_ms
            logger.debug("waiting_media_retry: still waiting message_id=%s retries=%d", mid, entry["retries"])

    if retried:
        # Reload index to get the latest state (avoid overwriting done marks)
        index = _load_waiting_media_index(runtime_root)
        for mid, entry in list(items.items()):
            if entry.get("retries", 0) > 0 and mid in index.get("items", {}):
                index["items"][mid]["retries"] = entry["retries"]
                index["items"][mid]["last_checked_at"] = entry["last_checked_at"]
        _save_waiting_media_index(runtime_root, index)
    return retried


def process_event_once(*, event, monitoring_plan: dict[str, Any], runtime_root: Path, logger: logging.Logger, apply_mode: bool = False, cursor: dict[str, Any] | None = None, cursor_path_override: Path | None = None, write_message_inbox: bool = True) -> dict[str, Any]:
    # R61: optionally write to message_inbox before any processing
    if write_message_inbox:
        try:
            from ops_hub.sop.message_inbox import ensure_message_inbox_schema, upsert_message_inbox_event
            ensure_message_inbox_schema()
            upsert_message_inbox_event(event)
        except Exception as exc:
            logger.warning("message_inbox: upsert failed for %s: %s", event.message_id, exc)

    # R67: text routing — classify text messages and write SOP fields back
    if write_message_inbox and event.message_type == "text" and event.text:
        try:
            from ops_hub.sop.text_router import classify_text_message, update_message_inbox_with_route
            route = classify_text_message(event)
            update_message_inbox_with_route(event.message_id, route)
            logger.info(
                "text_router: message_id=%s is_sop=%s project=%s flow=%s node=%s status=%s",
                event.message_id,
                route.is_sop_msg,
                route.sop_project_id,
                route.sop_flow,
                route.sop_node,
                route.processing_status,
            )
        except Exception as exc:
            logger.warning("text_router: failed for %s: %s", event.message_id, exc)

    # R59: check for waiting_media — skip OCR/VLM/executor but record event and advance cursor
    processing_status = event.metadata.get("processing_status", "ready")
    if processing_status == "waiting_media":
        event_path = _write_event_snapshot(event, runtime_root=runtime_root)
        _write_status_state(runtime_root, message_id=event.message_id, processed_at=_utc_now_iso())
        if cursor is not None:
            _update_cursor_for_event(runtime_root, cursor, event, cursor_path_override)
        # R59.1: record in waiting_media index for later retry
        _add_to_waiting_media_index(runtime_root, event, logger)
        logger.info(
            "waiting_media message_id=%s media_status=%s registration=%s event_path=%s",
            event.message_id,
            event.metadata.get("media_status"),
            event.raw_asset_bundle.registration_status if event.raw_asset_bundle else "unknown",
            event_path,
        )
        return {
            "event_path": event_path,
            "payload_paths": [],
            "state_paths": [],
            "workflow_tasks": 0,
            "todo_items": 0,
            "payloads_written": 0,
            "states_written": 0,
            "skipped_reason": "waiting_media",
        }

    event_path = _write_event_snapshot(event, runtime_root=runtime_root)
    _write_status_state(runtime_root, message_id=event.message_id, processed_at=_utc_now_iso())
    if cursor is not None:
        _update_cursor_for_event(runtime_root, cursor, event, cursor_path_override)
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

    # ── R52: run departure executor chain ─────────────────────────────
    try:
        exec_preview = run_departure_executor_chain_if_applicable(
            event, runtime_root=runtime_root, apply_mode=apply_mode
        )
        if exec_preview is not None and not exec_preview.skipped_reason:
            logger.info(
                "executor_runner message_id=%s status=%s depart=%s query=%d wagons=%s/%d",
                event.message_id,
                exec_preview.departure_status,
                exec_preview.departure_candidate.get("destination", "") if exec_preview.departure_candidate else "",
                exec_preview.query_total_candidates,
                exec_preview.wagon_status,
                exec_preview.wagon_planned_insert,
            )
        elif exec_preview is not None:
            logger.info(
                "executor_runner skipped message_id=%s reason=%s",
                event.message_id,
                exec_preview.skipped_reason,
            )
    except Exception as exc:
        logger.error("executor_runner failed message_id=%s: %s", event.message_id, exc)

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
    apply_mode: bool = False,
    cursor: dict[str, Any] | None = None,
    ignore_cursor: bool = False,
    replay_one: str | None = None,
    replay_from_id: int | None = None,
    cursor_path_override: Path | None = None,
    write_message_inbox: bool = True,
) -> int:
    watcher_args: dict[str, Any] = {}
    if replay_one:
        # Find the event by message_id
        watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)
        found = False
        for event in watcher.iter_message_events():
            if event.message_id == replay_one:
                found = True
                sop_watcher_obj = sop_watcher or _get_sop_watcher(fixture_dir)
                sop_watcher_obj.check_and_reload()
                plan = sop_watcher_obj.monitoring_plan
                process_event_once(
                    event=event, monitoring_plan=plan, runtime_root=runtime_root,
                    logger=logger, apply_mode=apply_mode,
                    cursor=None,  # replay-one does not update cursor by default
                    cursor_path_override=cursor_path_override,
                    write_message_inbox=write_message_inbox,
                )
                logger.info("replay-one message_id=%s processed", replay_one)
                break
        if not found:
            logger.warning("replay-one message_id=%s not found", replay_one)
        return 1 if found else 0

    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root, **watcher_args)
    sop_watcher_obj = sop_watcher or _get_sop_watcher(fixture_dir)
    sop_changed = sop_watcher_obj.check_and_reload()
    if sop_changed:
        logger.info("sop watcher: plan updated hash=%s projects=%s", sop_watcher_obj.runtime.sop_hash, sop_watcher_obj.runtime.loaded_projects)
    monitoring_plan = sop_watcher_obj.monitoring_plan
    seen = seen if seen is not None else set()
    processed = 0

    # Build skip map from cursor (per-source last_local_id)
    cursor_sources = (cursor or {}).get("sources", {}) if not ignore_cursor else {}

    for event in watcher.iter_message_events():
        local_id = event.metadata.get("local_id")
        if sync_start_id is not None and isinstance(local_id, int) and local_id < sync_start_id:
            continue

        # Cursor filtering: skip if local_id <= cursor's last_local_id for this source
        if not ignore_cursor and cursor_sources:
            source = str(event.metadata.get("source_file") or "")
            source_entry = cursor_sources.get(source, {})
            last_lid = source_entry.get("last_local_id", 0)
            lid_int = int(local_id) if not isinstance(local_id, int) else local_id
            if replay_from_id is not None:
                if isinstance(local_id, int) and local_id < replay_from_id:
                    continue
            elif lid_int <= last_lid:
                continue

        key = _seen_key(event)
        if key in seen:
            continue
        seen.add(key)
        process_event_once(
            event=event, monitoring_plan=monitoring_plan, runtime_root=runtime_root,
            logger=logger, apply_mode=apply_mode,
            cursor=cursor if not ignore_cursor else None,
            cursor_path_override=cursor_path_override,
            write_message_inbox=write_message_inbox,
        )
        processed += 1

    # ── R59.1: retry waiting_media whose images may now be available ───
    retried = _retry_waiting_media(
        runtime_root=runtime_root,
        chat_records_root=chat_records_root,
        monitoring_plan=monitoring_plan,
        logger=logger,
        apply_mode=apply_mode,
        cursor=cursor if not ignore_cursor else None,
        cursor_path_override=cursor_path_override,
    )
    if retried:
        processed += retried
        logger.info("waiting_media_retry: retried=%d", retried)

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
    apply_mode: bool = False,
    ignore_cursor: bool = False,
    reset_cursor_to_latest: bool = False,
    replay_one: str | None = None,
    replay_from_id: int | None = None,
    cursor_path_override: Path | None = None,
    write_message_inbox: bool = True,
) -> None:
    log_path = runtime_root / "live_service.log"
    logger = _configure_logging(log_path)

    # ── R18: write PID file ─────────────────────────────────────────────
    pid = os.getpid()
    is_transient = bool(reset_cursor_to_latest or replay_one)
    if not is_transient:
        pid_path = _write_pid_file(runtime_root, pid)
        logger.info("pid=%s pid_file=%s", pid, pid_path)
    else:
        logger.info("pid=%s (transient mode, skipping pid file)", pid)

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

    # ── R58: cursor management ──────────────────────────────────────────
    cursor_path = _cursor_path(runtime_root, cursor_path_override)
    if reset_cursor_to_latest:
        logger.info("cursor: resetting to latest (bootstrap)")
        cursor = _bootstrap_cursor_to_latest(runtime_root, chat_records_root, cursor_path_override)
        logger.info("cursor: reset complete sources=%d path=%s", len(cursor.get("sources", {})), cursor_path)
        if once:
            return
    elif not cursor_path.exists():
        logger.info("cursor: not found, bootstrapping to latest at=%s", cursor_path)
        cursor = _bootstrap_cursor_to_latest(runtime_root, chat_records_root, cursor_path_override)
        logger.info("cursor: bootstrap_cursor=true sources=%d", len(cursor.get("sources", {})))
    else:
        cursor = _load_cursor(runtime_root, cursor_path_override)
        logger.info("cursor: loaded sources=%d updated_at=%s", len(cursor.get("sources", {})), cursor.get("updated_at", ""))

    if ignore_cursor:
        logger.info("cursor: IGNORE_CURSOR enabled — will scan all messages")

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
            apply_mode=apply_mode,
            cursor=cursor if not ignore_cursor else None,
            ignore_cursor=ignore_cursor,
            replay_one=replay_one,
            replay_from_id=replay_from_id,
            cursor_path_override=cursor_path_override,
            write_message_inbox=write_message_inbox,
        )
        logger.info("poll complete processed=%s seen=%s", processed, len(seen))
        if once:
            return
        if max_iterations is not None and iterations >= max_iterations:
            return
        time.sleep(max(poll_interval, 0.1))


# ── R59.1: --check-waiting-media command ─────────────────────────────────

def _check_waiting_media_command(
    *,
    runtime_root: Path,
    chat_records_root: Path | None,
    fixture_dir: Path,
    apply_mode: bool,
) -> None:
    """Standalone command: check waiting_media index and retry ready items."""
    from ops_hub.sop.source_watcher import WxOpsSourceWatcher

    # Resolve chat_records_root
    if chat_records_root is None:
        wr = WxOpsSourceWatcher()
        chat_records_root = DEFAULT_CHAT_RECORDS_ROOT
    chat_records_root = Path(chat_records_root)

    log_path = runtime_root / "live_service.log"
    logger = _configure_logging(log_path)

    sop_watcher = _get_sop_watcher(fixture_dir)
    sop_watcher.check_and_reload()
    plan = sop_watcher.monitoring_plan

    index = _load_waiting_media_index(runtime_root)
    items = index.get("items", {})
    waiting = {mid: e for mid, e in items.items() if e.get("status") != "done"}
    logger.info("waiting_media_check: pending=%d total=%d", len(waiting), len(items))

    retried = _retry_waiting_media(
        runtime_root=runtime_root,
        chat_records_root=chat_records_root,
        monitoring_plan=plan,
        logger=logger,
        apply_mode=apply_mode,
        cursor=None,
    )
    result = {
        "action": "check_waiting_media",
        "retried": retried,
        "pending_before": len(waiting),
        "applied": apply_mode,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


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
        "fixture_dir": str(fixture_dir.resolve()) if fixture_dir else None,
        "last_log_line": last_log_line,
    }

    # ── R57: last processed tracking ─────────────────────────────────
    state = _read_status_state(runtime_root)
    if state:
        status["last_processed_message_id"] = state.get("last_processed_message_id", "")
        status["last_processed_time"] = state.get("last_processed_time", "")
    else:
        status["last_processed_message_id"] = ""
        status["last_processed_time"] = ""

    # ── R58: cursor section ──────────────────────────────────────────
    cursor_data = _load_cursor(runtime_root)
    cursor_path = _cursor_path(runtime_root)
    sources = cursor_data.get("sources", {})
    latest_msg = ""
    latest_lid = 0
    for src_entry in sources.values():
        lid = src_entry.get("last_local_id", 0)
        if lid > latest_lid:
            latest_lid = lid
            latest_msg = src_entry.get("last_message_id", "")
    status["cursor_path"] = str(cursor_path)
    status["cursor_exists"] = cursor_path.exists()
    status["cursor_source_count"] = len(sources)
    status["cursor_latest_message_id"] = latest_msg
    status["cursor_latest_local_id"] = latest_lid
    status["cursor_updated_at"] = cursor_data.get("updated_at", "")
    status["cursor_bootstrap"] = cursor_data.get("bootstrap_cursor", False)

    # ── R59.1: waiting_media_index section ────────────────────────────
    wm_index = _load_waiting_media_index(runtime_root)
    wm_items = wm_index.get("items", {})
    wm_waiting = sum(1 for e in wm_items.values() if e.get("status") != "done")
    wm_done = sum(1 for e in wm_items.values() if e.get("status") == "done")
    status["waiting_media_index"] = {
        "path": str(_waiting_media_index_path(runtime_root)),
        "exists": _waiting_media_index_path(runtime_root).exists(),
        "total": len(wm_items),
        "waiting": wm_waiting,
        "done": wm_done,
    }

    # ── R26: sop_runtime section ────────────────────────────────────
    if fixture_dir:
        try:
            sop_watcher = _get_sop_watcher(fixture_dir)
            sop_watcher.check_and_reload()
            status["sop_runtime"] = sop_watcher.status
        except Exception as exc:
            status["sop_runtime"] = {"error": str(exc)}

    # ── R34: sop_task_runtime section ───────────────────────────────
    if fixture_dir:
        try:
            from ops_hub.sop.sop_task_compiler import compile_project_sop

            task_plans = []
            for yaml_file in sorted(fixture_dir.glob("*.yaml")):
                plan = compile_project_sop(yaml_file)
                if plan.project_id:
                    task_plans.append(plan.summary())
            loaded_ids = [p["project_id"] for p in task_plans]
            total_missing = sum(p["missing"] for p in task_plans)
            total_implemented = sum(p["implemented"] for p in task_plans)
            status["sop_task_runtime"] = {
                "loaded_task_plans": len(task_plans),
                "project_ids": loaded_ids,
                "missing_task_count": total_missing,
                "implemented_task_count": total_implemented,
                "plans": task_plans,
            }
        except Exception as exc:
            status["sop_task_runtime"] = {"error": str(exc)}

    # ── R35: sop_task_trace_runtime section ──────────────────────────
    if fixture_dir:
        trace_dir = DEFAULT_RUNTIME_ROOT / "task_traces"
        try:
            trace_files = sorted(trace_dir.glob("*.json")) if trace_dir.exists() else []
            last_trace_count = len(trace_files)
            last_trace_id = trace_files[-1].stem if trace_files else ""
            status["sop_task_trace_runtime"] = {
                "enabled": True,
                "trace_dir": str(trace_dir.resolve()),
                "last_trace_count": last_trace_count,
                "last_trace_id": last_trace_id,
            }
        except Exception as exc:
            status["sop_task_trace_runtime"] = {"error": str(exc)}

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
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply mode: write wagon_shipments to DB, generate Excel, upload to factory",
    )
    # ── R58: cursor and replay control ──────────────────────────────────
    parser.add_argument(
        "--ignore-cursor",
        action="store_true",
        help="Ignore persistent cursor and scan all messages (do not use in launchd)",
    )
    parser.add_argument(
        "--reset-cursor-to-latest",
        action="store_true",
        help="Reset cursor to latest message in each source, then exit (no processing)",
    )
    parser.add_argument(
        "--replay-one",
        type=str,
        default=None,
        help="Replay a single message by message_id (e.g. wx_2206); does not modify cursor",
    )
    parser.add_argument(
        "--replay-from-id",
        type=int,
        default=None,
        help="Replay messages from local_id >= N; cursor is still respected per-source",
    )
    parser.add_argument(
        "--cursor-path",
        type=Path,
        default=None,
        help="Override cursor file path (default: runtime/cursors/live_service_cursor.json)",
    )
    # ── R59.1: waiting_media retry ─────────────────────────────────────
    parser.add_argument(
        "--check-waiting-media",
        action="store_true",
        help="Check waiting_media index for newly available images and retry them, then exit",
    )
    # ── R61: message_inbox ──────────────────────────────────────────────
    parser.add_argument(
        "--write-message-inbox",
        action="store_true",
        help="Write each processed event to message_inbox table in sop_agent.db",
    )
    # ── R61.1: opt-out (default is now ON) ─────────────────────────────
    parser.add_argument(
        "--no-write-message-inbox",
        action="store_true",
        help="Disable writing to message_inbox (default: enabled)",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if args.status:
        status_command(runtime_root=args.runtime_root, fixture_dir=args.fixture_dir)
        return

    if args.check_waiting_media:
        _check_waiting_media_command(
            runtime_root=args.runtime_root,
            chat_records_root=args.chat_records_root,
            fixture_dir=args.fixture_dir,
            apply_mode=args.apply,
        )
        return

    # Resolve chat_records_root: explicit arg > env var > canonical default
    if args.chat_records_root:
        chat_records_root = args.chat_records_root
    else:
        watcher = WxOpsSourceWatcher()
        chat_records_root = DEFAULT_CHAT_RECORDS_ROOT

    run_live_service(
        chat_records_root=chat_records_root,
        runtime_root=args.runtime_root,
        fixture_dir=args.fixture_dir,
        poll_interval=args.poll_interval,
        once=args.once,
        max_iterations=args.max_iterations,
        sync_start_id=args.sync_start_id,
        apply_mode=args.apply,
        ignore_cursor=args.ignore_cursor,
        reset_cursor_to_latest=args.reset_cursor_to_latest,
        replay_one=args.replay_one,
        replay_from_id=args.replay_from_id,
        cursor_path_override=args.cursor_path,
        write_message_inbox=not args.no_write_message_inbox,
    )


if __name__ == "__main__":
    main()
