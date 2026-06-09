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

from sop_hub.utils.time import now_iso_beijing as _now_iso_beijing

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

from sop_hub.sop.dashboard_payload_queue import build_dashboard_payload_queue, write_dashboard_payload_queue
# dashboard_state_preview removed 2026-06-06: 老 HTML 看板 JSON 预览已废,
# CLI dashboard 直查 sop_agent.db,中间 JSON 不需要了
from sop_hub.sop.monitoring_plan_matcher import match_message_event
from sop_hub.sop.monitoring_plan_preview import build_real_sop_monitoring_plan_preview
from sop_hub.sop.executor_runner import run_departure_executor_chain_if_applicable
from sop_hub.sop.sop_watcher import SopWatcher
from sop_hub.sop.source_watcher import WxOpsSourceWatcher
from sop_hub.sop.workflow_task import build_workflow_task_queue

# ── R52: executor runner (disabled R69 — now driven by workflow_task_db) ─
# from sop_hub.sop.executor_runner import run_departure_executor_chain_if_applicable

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

    return _now_iso_beijing()


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
    logger = logging.getLogger("sop_hub.live_service")
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
    ]
    for d in required_dirs:
        d.mkdir(parents=True, exist_ok=True)
        if not d.is_dir():
            raise NotADirectoryError(f"Failed to create runtime directory: {d}")

    logger.info("startup validation passed runtime_root=%s chat_records_root=%s", runtime_root, chat_records_root)


# ── R59.1: waiting_media index (retry for images whose download completes later) ──

# 2026-06-06(#109):key 改为 group_name:month_stem:local_id 复合键。
# 旧用 event.message_id (wx_<seq>),seq 每月归 0,跨月撞同名 → retry path 拿到
# 过期月份的 source_file → 永远找不到 payload → 死循环 retry。
# 新 key 直接锁定 jsonl 文件 + 文件内 local_id,全局唯一,无碰撞。
# 同时加 max_retries 阈值,超阈值直接标 abandoned,避免历史死任务占用 retry 轮。
_WAITING_MEDIA_MAX_RETRIES = 200


def _waiting_media_index_path(runtime_root: Path) -> Path:
    return runtime_root / "waiting_media_index.json"


def _compute_todo_key(source_file: str | Path, local_id: Any) -> str:
    """Globally-unique key for a waiting_media entry.

    格式 ``<group_name>:<jsonl_stem>:<local_id>``,例:
    ``铁晟业务工作群:2026-06:313``。group + month + local_id 三段唯一锁定一条消息。
    """
    p = Path(str(source_file)) if source_file else Path("")
    group_name = p.parent.name if p.parent.name and p.parent.name != "." else ""
    stem = p.stem if p.name else ""
    lid_str = "" if local_id is None else str(local_id)
    return f"{group_name}:{stem}:{lid_str}"


def _load_waiting_media_index(runtime_root: Path) -> dict[str, Any]:
    path = _waiting_media_index_path(runtime_root)
    if not path.exists():
        return {"version": 2, "items": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 2, "items": {}}


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
    source_file = event.metadata.get("source_file", "")
    local_id = event.metadata.get("local_id")
    todo_key = _compute_todo_key(source_file, local_id)
    if todo_key in index.get("items", {}):
        entry = index["items"][todo_key]
        entry["retries"] = entry.get("retries", 0) + 1
        entry["last_checked_at"] = _utc_now_iso()
    else:
        index["items"][todo_key] = {
            "todo_key": todo_key,
            "message_id": event.message_id,
            "source_file": source_file,
            "local_id": local_id,
            "group_name": event.metadata.get("group_name", ""),
            "image_md5": event.metadata.get("image_md5", ""),
            "msg_path": event.metadata.get("msg_path", ""),
            "media_status": event.metadata.get("media_status", ""),
            "added_at": _utc_now_iso(),
            "retries": 0,
            "status": "waiting",
        }
        logger.info("waiting_media_index: added todo_key=%s message_id=%s", todo_key, event.message_id)
    index["items"][todo_key]["last_checked_at"] = _utc_now_iso()
    _save_waiting_media_index(runtime_root, index)


def _mark_waiting_media_done(runtime_root: Path, todo_key: str, logger: logging.Logger, index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark a waiting_media entry as done. ``todo_key`` 是 items dict 的真实 key。"""
    if index is None:
        index = _load_waiting_media_index(runtime_root)
    if todo_key in index.get("items", {}):
        index["items"][todo_key]["status"] = "done"
        index["items"][todo_key]["resolved_at"] = _utc_now_iso()
        _save_waiting_media_index(runtime_root, index)
        logger.info("waiting_media_index: marked done todo_key=%s", todo_key)
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
    from sop_hub.sop.source_watcher import WxOpsSourceWatcher

    index = _load_waiting_media_index(runtime_root)
    items = index.get("items", {})
    retried = 0
    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)

    for mid, entry in list(items.items()):
        if entry.get("status") in ("done", "abandoned"):
            continue

        # #109:retry 超阈值 → 标 abandoned。绝大多数死任务是跨月 source_file 撞名
        # (旧版 wx_<seq> key)或 jsonl 已轮转 — 无脑 retry 永远不会成功。
        retries_now = int(entry.get("retries") or 0)
        if retries_now >= _WAITING_MEDIA_MAX_RETRIES:
            logger.warning(
                "waiting_media_retry: max retries reached (%d) for mid=%s, marking abandoned",
                retries_now, mid,
            )
            entry["status"] = "abandoned"
            entry["abandoned_at"] = _utc_now_iso()
            continue

        source_file = entry.get("source_file", "")
        local_id = entry.get("local_id")
        if not source_file:
            logger.warning("waiting_media_retry: no source_file for mid=%s, marking done", mid)
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
            entry["retries"] = retries_now + 1
            entry["last_checked_at"] = _utc_now_iso()
            continue

        # Re-build event through source_watcher (re-evaluates image path)
        new_event = watcher._build_event(source_path=src_path, payload=payload)
        new_ms = new_event.metadata.get("media_status", "")
        new_ps = new_event.metadata.get("processing_status", "")

        if new_ps != "waiting_media":
            # Media is now available — process it
            logger.info(
                "waiting_media_retry: media ready mid=%s media_status=%s -> %s",
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
            entry["retries"] = retries_now + 1
            entry["last_checked_at"] = _utc_now_iso()
            entry["media_status"] = new_ms
            logger.debug("waiting_media_retry: still waiting mid=%s retries=%d", mid, entry["retries"])

    if retried:
        # Reload index to get the latest state (avoid overwriting done marks)
        index = _load_waiting_media_index(runtime_root)
        for mid, entry in list(items.items()):
            if entry.get("retries", 0) > 0 and mid in index.get("items", {}):
                index["items"][mid]["retries"] = entry["retries"]
                index["items"][mid]["last_checked_at"] = entry["last_checked_at"]
        _save_waiting_media_index(runtime_root, index)
    return retried

# ── SOP trigger categories for auto-classification ──────────────────────
_SOP_TRIGGER_CATEGORIES = {"出港计划通知单", "检装车通知单"}

# ── Category → SOP mapping ──────────────────────────────────────────────
_CATEGORY_SOP_MAP: dict[str, dict[str, str]] = {
    "出港计划通知单": {
        "sop_flow": "departure_flow",
        "sop_node": "create_release_batch",
    },
    "检装车通知单": {
        "sop_flow": "inspection_notice_flow",
        "sop_node": "create_inspection_candidate",
    },
}


def _update_inbox_classification(
    message_id: str,
    category: str,
    *,
    extraction_json_path: str = "",
    batch_ids: list[str] | None = None,
    ingested_count: int = 0,
) -> None:
    """Update message_inbox with classification results after VLM processing."""
    import sqlite3

    db_path = CANONICAL_REPO_ROOT / "data" / "sop_agent.db"
    if not db_path.exists():
        return

    sop_map = _CATEGORY_SOP_MAP.get(category, {})
    is_sop = 1 if sop_map else 0
    sop_flow = sop_map.get("sop_flow", "")
    sop_node = sop_map.get("sop_node", "")

    processing_status = "task_succeeded" if ingested_count > 0 else "classified"
    db_action = "release_batch_ingested" if ingested_count > 0 else ""

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """UPDATE message_inbox SET
            classification_label = ?,
            classification_status = 'classified',
            extraction_json_path = ?,
            is_sop_msg = ?,
            sop_flow = CASE WHEN ? != '' THEN ? ELSE sop_flow END,
            sop_node = CASE WHEN ? != '' THEN ? ELSE sop_node END,
            processing_status = ?,
            db_action = CASE WHEN ? != '' THEN ? ELSE db_action END,
            db_record_ids = CASE WHEN ? != '' THEN ? ELSE db_record_ids END,
            updated_at = ?
        WHERE message_id = ?""",
        (
            category,
            extraction_json_path,
            is_sop,
            sop_flow, sop_flow,
            sop_node, sop_node,
            processing_status,
            db_action, db_action,
            json.dumps(batch_ids or []), json.dumps(batch_ids or []),
            _utc_now_iso(),
            message_id,
        ),
    )
    conn.commit()
    conn.close()


class InboxWriteRetryNeeded(Exception):
    """#117: message_inbox upsert / text_router 失败时抛出。

    调用方(_poll_sources)接住后:不前进 cursor,下一轮 polling 重试。
    """


def process_event_once(*, event, monitoring_plan: dict[str, Any], runtime_root: Path, logger: logging.Logger, apply_mode: bool = False, cursor: dict[str, Any] | None = None, cursor_path_override: Path | None = None, write_message_inbox: bool = True) -> dict[str, Any]:
    # R61: optionally write to message_inbox before any processing
    # #117 (2026-06-07): inbox upsert / text_router classify 之前是 try/except 吞掉
    # warning,cursor 仍单调推进 → 这条消息**永远不会被重试**。当 daemon 自锁/
    # sqlite locked 等瞬态故障来临时,SOP 会跳过本应触发的链(蓝鳍 wx_367 / 中唐
    # 鞍子河 inbox 88 都是这条根因)。
    # 新规则:write_message_inbox=True 时,upsert 或 text_router 任一失败 → 抛
    # InboxWriteRetryNeeded,调用方(_poll_sources)负责不前进 cursor,下一轮重试。
    inbox_write_failed_reason: str | None = None
    if write_message_inbox:
        try:
            from sop_hub.sop.message_inbox import ensure_message_inbox_schema, upsert_message_inbox_event
            ensure_message_inbox_schema()
            upsert_message_inbox_event(event)
        except Exception as exc:
            logger.warning("message_inbox: upsert failed for %s: %s", event.message_id, exc)
            inbox_write_failed_reason = f"upsert failed: {exc}"

    # R67: text routing — classify text messages and write SOP fields back
    if (write_message_inbox and event.message_type == "text" and event.text
            and inbox_write_failed_reason is None):
        try:
            from sop_hub.sop.text_router import classify_text_message, update_message_inbox_with_route
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
            inbox_write_failed_reason = f"text_router failed: {exc}"

    if inbox_write_failed_reason is not None:
        # 关键:不推 cursor、不生成 workflow_task,留给下一轮重试
        raise InboxWriteRetryNeeded(
            f"message_id={event.message_id}: {inbox_write_failed_reason}"
        )

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

    # ── Image auto-processing: VLM classification + OCR extraction + DB ingestion ──
    # When an image message has media available, run the full pipeline:
    #   classify → extract → ingest_release_batch (for 出港计划通知单)
    #   classify → extract → ingest_inspection (for 检装车通知单)
    image_auto_result: dict[str, Any] | None = None
    if event.message_type == "image" and event.raw_asset_bundle:
        _image_path = getattr(event.raw_asset_bundle, "raw_image_path", None)
        if _image_path and Path(str(_image_path)).exists():
            try:
                from sop_hub.config import load_settings
                from sop_hub.runner import process_new_image

                _settings = load_settings()
                # Normalize group_name: strip -GROUPxxx suffix (e.g. "数据单发群-GROUP013" → "数据单发群")
                _raw_group = str(event.metadata.get("group_name") or event.group_id or "")
                _normalized_group = _raw_group.split("-GROUP")[0] if "-GROUP" in _raw_group else _raw_group
                _proc_result = process_new_image(
                    str(_image_path),
                    _settings,
                    group_name=_normalized_group,
                )
                _category = _proc_result.category or ""
                # Ingestion results are stored inside the extracted dict
                _extracted = _proc_result.extracted or {}
                _ingested = _extracted.get("_agent_ingested", 0)
                _batch_ids = _extracted.get("_agent_updated_ids", [])
                _ext_json = _proc_result.extraction_saved_path or ""
                logger.info(
                    "image_auto: message_id=%s category=%s ingested=%s batch_ids=%s ext_path=%s",
                    event.message_id, _category, _ingested, _batch_ids, _ext_json,
                )
                image_auto_result = {
                    "category": _category,
                    "ingested": _ingested,
                    "batch_ids": _batch_ids,
                    "extraction_json_path": _ext_json,
                }
                if _proc_result.error:
                    image_auto_result["error"] = _proc_result.error
                # Update message_inbox with classification + ingestion results
                if write_message_inbox and _category:
                    try:
                        _update_inbox_classification(
                            event.message_id,
                            _category,
                            extraction_json_path=_ext_json,
                            batch_ids=_batch_ids,
                            ingested_count=_ingested or 0,
                        )
                    except Exception as _inbox_exc:
                        logger.warning(
                            "image_auto: inbox update failed for %s: %s",
                            event.message_id, _inbox_exc,
                        )
                        # #117: inbox 写失败 → 不推 cursor,让下一轮重试。
                        # 否则 classification/extraction 信息丢失,中唐 inbox 88
                        # 的 sop_project_id / extraction_json_path 漏写就是这条根因。
                        raise InboxWriteRetryNeeded(
                            f"image_auto inbox update failed for {event.message_id}: {_inbox_exc}"
                        )
            except Exception as exc:
                logger.warning("image_auto: processing failed for %s: %s", event.message_id, exc)
                image_auto_result = {"error": str(exc)}

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
    # 2026-06-06:删 build/write_dashboard_state_preview(老 HTML 看板配套)
    state_paths: list = []

    logger.info(
        "processed event message_id=%s group_id=%s payloads=%s event_path=%s",
        event.message_id,
        event.group_id,
        len(payload_paths),
        event_path,
    )

    # ── R69: executor_runner is now driven by workflow_task_db, not direct text matching ──
    # The old R52 direct trigger (run_departure_executor_chain_if_applicable with "四平" gate)
    # is disabled. Tasks are created by workflow_task_store and executed by workflow_task_executor.
    # To execute pending tasks: python -m sop_hub.sop.workflow_task_executor --run-pending

    result = {
        "event_path": event_path,
        "payload_paths": payload_paths,
        "state_paths": state_paths,
        "workflow_tasks": len(workflow_queue.workflow_tasks),
        "todo_items": len(workflow_queue.todo_items),
        "payloads_written": len(payload_paths),
        "states_written": len(state_paths),
    }
    if image_auto_result:
        result["image_auto"] = image_auto_result
    return result


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
        try:
            process_event_once(
                event=event, monitoring_plan=monitoring_plan, runtime_root=runtime_root,
                logger=logger, apply_mode=apply_mode,
                cursor=cursor if not ignore_cursor else None,
                cursor_path_override=cursor_path_override,
                write_message_inbox=write_message_inbox,
            )
            processed += 1
        except InboxWriteRetryNeeded as exc:
            # #117: inbox 写失败 → cursor 不前进,seen 也撤回让下一轮可重试。
            # 否则瞬态 db lock 会让单条消息永久消失。
            logger.warning("inbox_retry_needed: %s (cursor not advanced)", exc)
            seen.discard(key)
            # 不 break:同 poll 轮里别的 event 还能继续;cursor 由 _update_cursor_for_event
            # 单条 advance,这条没 advance 就还在 last_local_id 后头,下轮自然重试。

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
        # 2026-06-02: ensure_dispatch_board_data hook 移除。
        # 老 HTML 看板退役,改 scripts/cli_dashboard.py(直查 sqlite,无中间 JSON)。
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
    from sop_hub.sop.source_watcher import WxOpsSourceWatcher

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
            from sop_hub.sop.sop_task_compiler import compile_project_sop

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
