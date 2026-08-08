"""Functional test for read-only wx-ops-agent source supervision (portable).

Uses tmp chat_records / image roots — no sibling wx-ops-agent checkout and no
~/Documents/bussiness-artifacts dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

from sop_hub.sop.source_watcher import WxOpsSourceWatcher


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_wx_ops_source_watcher_emits_message_events_with_metadata_and_image_paths(
    tmp_path: Path,
):
    chat_root = tmp_path / "chat_records"
    image_root = tmp_path / "wechat_images"
    image_root.mkdir(parents=True)
    log_path = tmp_path / "daemon-auto.log"
    log_path.write_text("ok\n", encoding="utf-8")

    img = image_root / "5f120e462f4d46a6362f9a39fa19ebfc.jpg"
    img.write_bytes(b"fake-jpeg")

    group = "铁晟业务工作群"
    jsonl = chat_root / group / "2026-05.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "local_id": 1,
                "seq": 1,
                "group_name": group,
                "group_wxid": group,
                "msg-type": "text",
                "msg-content": "hello",
                "time": "2026-05-01 10:00:00",
                "server_id": 1001,
                "message_key": "mk-1",
            },
            {
                "local_id": 13,
                "seq": 13,
                "group_name": group,
                "group_wxid": group,
                "msg-type": "image",
                "msg-path": str(img),
                "image_md5": "5f120e462f4d46a6362f9a39fa19ebfc",
                "time": "2026-05-01 11:00:00",
                "server_id": 1013,
                "message_key": "mk-13",
            },
        ],
    )

    watcher = WxOpsSourceWatcher(
        chat_records_root=chat_root,
        image_root=image_root,
        daemon_log_path=log_path,
    )

    snapshot = watcher.snapshot()
    assert snapshot["chat_records_root"] == str(chat_root)
    assert snapshot["image_root"] == str(image_root)
    assert snapshot["daemon_log"]["path"] == str(log_path)
    assert snapshot["daemon_log"]["exists"] is True

    events = list(
        watcher.iter_message_events(
            group_name=group,
            source_file_path=jsonl,
            limit=20,
        )
    )
    assert len(events) == 2

    first = events[0]
    assert first.message_id == "wx_2026-05_1"
    assert first.channel == "wechat"
    assert first.source_agent == "wx-ops-agent"
    assert first.group_id == group
    assert first.metadata["local_id"] == 1
    assert "server_id" in first.metadata
    assert "message_key" in first.metadata
    assert "image_md5" in first.metadata

    image_event = next(event for event in events if event.message_id == "wx_2026-05_13")
    assert image_event.metadata["local_id"] == 13
    assert image_event.metadata["image_md5"] == "5f120e462f4d46a6362f9a39fa19ebfc"
    assert image_event.raw_asset_bundle is not None
    raw_path = image_event.raw_asset_bundle.asset_paths["raw_image_path"]
    assert raw_path is not None
    assert raw_path.startswith(str(image_root))
    assert image_event.raw_asset_bundle.message_metadata_path == str(jsonl)


def test_source_watcher_env_overrides(monkeypatch, tmp_path: Path):
    """Env roots beat sibling-layout defaults (portable contract)."""
    chat = tmp_path / "chat"
    chat.mkdir()
    img = tmp_path / "img"
    img.mkdir()
    log = tmp_path / "d.log"
    log.write_text("", encoding="utf-8")

    monkeypatch.setenv("WX_OPS_AGENT_CHAT_RECORDS_DIR", str(chat))
    monkeypatch.setenv("WX_OPS_AGENT_WECHAT_IMAGE_ROOT", str(img))
    monkeypatch.setenv("WX_OPS_AGENT_DAEMON_LOG_PATH", str(log))

    # Reload defaults by constructing with defaults after env set — dataclass
    # defaults are evaluated at class body time, so pass explicit paths via
    # the module-level default functions.
    from sop_hub.sop import source_watcher as sw

    assert sw._default_chat_records_root() == chat
    assert sw._default_image_root() == img
    assert sw._default_daemon_log_path() == log
