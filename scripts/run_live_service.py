#!/usr/bin/env python3
"""Live service bootstrap for SOP Data Hub.

This script keeps the chain local-only:
WxOpsSourceWatcher -> MessageEvent -> RawAssetBundle -> Matcher -> WorkflowTask
-> DashboardPayloadQueue -> DashboardState.

It polls `data/chat_records/**/*.jsonl` continuously, writes event snapshots under
`runtime/events/`, payload JSON under `runtime/dashboard_intents/`, and state JSON
under `runtime/dashboard_state/`.
"""

from __future__ import annotations

import argparse
import json
import logging
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
from ops_hub.sop.source_watcher import WxOpsSourceWatcher
from ops_hub.sop.workflow_task import build_workflow_task_queue

DEFAULT_RUNTIME_ROOT = REPO_ROOT / "runtime"
DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "sops"
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_LOG_PATH = DEFAULT_RUNTIME_ROOT / "live_service.log"


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
) -> int:
    watcher = WxOpsSourceWatcher(chat_records_root=chat_records_root)
    monitoring_plan = _load_monitoring_plan(fixture_dir)
    seen = seen if seen is not None else set()
    processed = 0

    for event in watcher.iter_message_events():
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
) -> None:
    log_path = runtime_root / "live_service.log"
    logger = _configure_logging(log_path)
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
        )
        logger.info("poll complete processed=%s seen=%s", processed, len(seen))
        if once:
            return
        if max_iterations is not None and iterations >= max_iterations:
            return
        time.sleep(max(poll_interval, 0.1))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SOP Data Hub live service.")
    parser.add_argument(
        "--chat-records-root",
        type=Path,
        default=None,
        help="Path to wx-ops-agent data/chat_records root (default: watcher default)",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Output root for runtime/events, runtime/dashboard_intents, runtime/dashboard_state",
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
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    watcher = WxOpsSourceWatcher(chat_records_root=args.chat_records_root) if args.chat_records_root else WxOpsSourceWatcher()
    run_live_service(
        chat_records_root=watcher.chat_records_root,
        runtime_root=args.runtime_root,
        fixture_dir=args.fixture_dir,
        poll_interval=args.poll_interval,
        once=args.once,
        max_iterations=args.max_iterations,
    )


if __name__ == "__main__":
    main()
