"""Functional test for read-only wx-ops-agent source supervision.

Scope:
- read chat records from `data/chat_records/**/*.jsonl`
- resolve images from `~/Documents/bussiness-artifacts/wechat_images`
- read daemon logs from `data/runtime-logs/daemon-auto.log`
- preserve stable metadata without touching runtime, DB, or wx-ops-agent
"""

from pathlib import Path

from sop_hub.sop.source_watcher import WxOpsSourceWatcher


WX_OPS_AGENT_ROOT = Path(__file__).resolve().parents[2].parent / "wx-ops-agent"
CHAT_RECORDS_ROOT = WX_OPS_AGENT_ROOT / "data" / "chat_records"
IMAGE_ROOT = Path.home() / "Documents" / "bussiness-artifacts" / "wechat_images"
DAEMON_LOG_PATH = WX_OPS_AGENT_ROOT / "data" / "runtime-logs" / "daemon-auto.log"


def test_wx_ops_source_watcher_emits_message_events_with_metadata_and_image_paths():
    watcher = WxOpsSourceWatcher(
        chat_records_root=CHAT_RECORDS_ROOT,
        image_root=IMAGE_ROOT,
        daemon_log_path=DAEMON_LOG_PATH,
    )

    snapshot = watcher.snapshot()
    assert snapshot["chat_records_root"] == str(CHAT_RECORDS_ROOT)
    assert snapshot["image_root"] == str(IMAGE_ROOT)
    assert snapshot["daemon_log"]["path"] == str(DAEMON_LOG_PATH)
    assert snapshot["daemon_log"]["exists"] is True

    events = list(
        watcher.iter_message_events(
            group_name="铁晟业务工作群",
            source_file_path=CHAT_RECORDS_ROOT / "铁晟业务工作群" / "2026-05.jsonl",
            limit=20,
        )
    )
    assert events

    first = events[0]
    assert first.message_id == "wx_1"
    assert first.channel == "wechat"
    assert first.source_agent == "wx-ops-agent"
    assert first.group_id == "铁晟业务工作群"
    assert first.metadata["local_id"] == 1
    assert "server_id" in first.metadata
    assert "message_key" in first.metadata
    assert "image_md5" in first.metadata

    image_event = next(event for event in events if event.message_id == "wx_13")
    assert image_event.metadata["local_id"] == 13
    assert image_event.metadata["image_md5"] == "5f120e462f4d46a6362f9a39fa19ebfc"
    assert image_event.raw_asset_bundle is not None
    assert image_event.raw_asset_bundle.asset_paths["raw_image_path"] is None or image_event.raw_asset_bundle.asset_paths["raw_image_path"].startswith(str(IMAGE_ROOT))
    assert image_event.raw_asset_bundle.message_metadata_path == str(
        CHAT_RECORDS_ROOT / "铁晟业务工作群" / "2026-05.jsonl"
    )
