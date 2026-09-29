"""The retry counter must survive a live-service restart even without success."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import scripts.run_live_service as live_service
from scripts.run_live_service import (
    _WAITING_MEDIA_RETRY_INTERVAL_SECONDS,
    _retry_waiting_media,
    _save_waiting_media_index,
)


def test_waiting_media_retry_is_persisted_when_payload_is_not_available(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    missing_source = tmp_path / "not-yet-written.jsonl"
    _save_waiting_media_index(runtime, {
        "items": {
            "group-a:wx_1": {
                "status": "waiting", "source_file": str(missing_source),
                "local_id": 1, "retries": 0,
            }
        }
    })
    assert _retry_waiting_media(
        runtime_root=runtime, chat_records_root=tmp_path / "records",
        monitoring_plan={}, logger=logging.getLogger("test"), apply_mode=False,
    ) == 0
    saved = json.loads((runtime / "waiting_media_index.json").read_text(encoding="utf-8"))
    entry = saved["items"]["group-a:wx_1"]
    assert entry["retries"] == 1
    assert entry["last_checked_at"]


def test_waiting_media_uses_elapsed_time_and_rate_limits_retries(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    missing_source = tmp_path / "not-yet-written.jsonl"
    now = datetime.now(UTC).isoformat()
    _save_waiting_media_index(runtime, {
        "items": {
            "group-a:wx_2": {
                "status": "waiting", "source_file": str(missing_source),
                "local_id": 2, "retries": 200, "added_at": now,
                "last_checked_at": now,
            }
        }
    })

    assert _retry_waiting_media(
        runtime_root=runtime, chat_records_root=tmp_path / "records",
        monitoring_plan={}, logger=logging.getLogger("test"), apply_mode=False,
    ) == 0
    saved = json.loads((runtime / "waiting_media_index.json").read_text(encoding="utf-8"))
    entry = saved["items"]["group-a:wx_2"]
    assert entry["status"] == "waiting"
    assert entry["retries"] == 200

    # The persisted throttle makes a second immediate poll a no-op as well.
    assert _WAITING_MEDIA_RETRY_INTERVAL_SECONDS > 0


def test_waiting_media_keeps_ready_image_pending_when_classification_fails(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = tmp_path / "records" / "group-a" / "2026-09.jsonl"
    source.parent.mkdir(parents=True)
    image = tmp_path / "image.jpg"
    image.write_bytes(b"not-decoded-in-this-test")
    source.write_text(json.dumps({
        "local_id": 2,
        "msg-type": "image",
        "msg-path": str(image),
        "time": "2026-09-29 12:00:00",
    }) + "\n", encoding="utf-8")
    key = "group-a:2026-09:2"
    _save_waiting_media_index(runtime, {
        "items": {
            key: {
                "status": "waiting", "source_file": str(source),
                "local_id": 2, "retries": 0, "added_at": datetime.now(UTC).isoformat(),
            }
        }
    })

    monkeypatch.setattr(
        live_service,
        "process_event_once",
        lambda **_: {"image_auto": {"category": "error", "error": "HTTP 500"}},
    )
    assert _retry_waiting_media(
        runtime_root=runtime, chat_records_root=tmp_path / "records",
        monitoring_plan={}, logger=logging.getLogger("test"), apply_mode=False,
    ) == 0
    saved = json.loads((runtime / "waiting_media_index.json").read_text(encoding="utf-8"))
    entry = saved["items"][key]
    assert entry["status"] == "waiting"
    assert entry["retries"] == 1
    assert entry["last_error"] == "HTTP 500"
