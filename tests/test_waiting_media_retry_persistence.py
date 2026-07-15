"""The retry counter must survive a live-service restart even without success."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts.run_live_service import _retry_waiting_media, _save_waiting_media_index


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
