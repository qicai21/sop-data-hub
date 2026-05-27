"""Read-only source watcher for wx-ops-agent chat-record supervision.

This module stays local-only:
- read chat records from `data/chat_records/**/*.jsonl`;
- resolve image paths under `~/Documents/bussiness-artifacts/wechat_images`;
- inspect daemon logs under `data/runtime-logs/daemon-auto.log`;
- emit `MessageEvent` objects with stable message_id and preserved metadata;
- do not touch runtime, database, or wx-ops-agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from ops_hub.sop.monitoring_plan_matcher import MessageEvent
from ops_hub.sop.raw_asset_bundle import RawAssetBundle


_DEFAULT_WECHAT_IMAGE_ROOT = Path.home() / "Documents" / "bussiness-artifacts" / "wechat_images"


def _default_wx_ops_agent_root() -> Path:
    env = os.environ.get("WX_OPS_AGENT_ROOT")
    if env:
        return Path(env).expanduser()
    # __file__ = repos/ops-data-hub/src/ops_hub/sop/source_watcher.py
    # parents[4] = repos/ → sibling wx-ops-agent is at repos/wx-ops-agent
    return Path(__file__).resolve().parents[4] / "wx-ops-agent"


def _default_chat_records_root() -> Path:
    env = os.environ.get("WX_OPS_AGENT_CHAT_RECORDS_DIR")
    if env:
        return Path(env).expanduser()
    return _default_wx_ops_agent_root() / "data" / "chat_records"


def _default_daemon_log_path() -> Path:
    env = os.environ.get("WX_OPS_AGENT_DAEMON_LOG_PATH")
    if env:
        return Path(env).expanduser()
    return _default_wx_ops_agent_root() / "data" / "runtime-logs" / "daemon-auto.log"


def _default_image_root() -> Path:
    env = os.environ.get("WX_OPS_AGENT_WECHAT_IMAGE_ROOT")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_WECHAT_IMAGE_ROOT


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _extract_image_md5(message_path: str, payload: dict[str, Any]) -> str:
    for key in ("image_md5", "image_key", "fullmd5", "md5"):
        value = _normalize_text(payload.get(key))
        if value:
            return value
    name = Path(message_path).name
    stem = Path(message_path).stem
    for candidate in (stem, name):
        if "_" in candidate:
            tail = candidate.rsplit("_", 1)[-1].strip()
            if tail:
                return tail.split(".", 1)[0]
    return ""


@dataclass(frozen=True)
class WxOpsSourceWatcher:
    """Read-only watcher for wx-ops-agent source records."""

    chat_records_root: Path = _default_chat_records_root()
    image_root: Path = _default_image_root()
    daemon_log_path: Path = _default_daemon_log_path()
    source_agent: str = "wx-ops-agent"
    channel: str = "wechat"

    def snapshot(self) -> dict[str, Any]:
        chat_files = sorted(self.chat_records_root.rglob("*.jsonl")) if self.chat_records_root.exists() else []
        return {
            "chat_records_root": str(self.chat_records_root),
            "image_root": str(self.image_root),
            "daemon_log": {
                "path": str(self.daemon_log_path),
                "exists": self.daemon_log_path.exists(),
            },
            "chat_record_file_count": len(chat_files),
        }

    def _iter_payloads(
        self,
        *,
        group_name: str | None = None,
        source_file_stem: str | None = None,
        source_file_path: str | Path | None = None,
    ) -> Iterator[tuple[Path, dict[str, Any]]]:
        if not self.chat_records_root.exists():
            return
        requested_source_file = Path(source_file_path).expanduser().resolve() if source_file_path else None
        for jsonl_path in sorted(self.chat_records_root.rglob("*.jsonl")):
            if requested_source_file is not None and jsonl_path.resolve() != requested_source_file:
                continue
            relative_parts = jsonl_path.relative_to(self.chat_records_root).parts
            if not relative_parts:
                continue
            if source_file_stem and jsonl_path.stem != source_file_stem:
                continue
            source_group_name = relative_parts[0]
            if group_name and source_group_name != group_name:
                continue
            with jsonl_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        yield jsonl_path, payload

    def _build_event(self, *, source_path: Path, payload: dict[str, Any]) -> MessageEvent:
        local_id = _coerce_int(payload.get("local_id"))
        if local_id is None:
            local_id = _coerce_int(payload.get("seq")) or 0
        message_id = f"wx_{local_id}"

        group_name = _normalize_text(payload.get("group_name") or source_path.parent.name)
        group_id = _normalize_text(payload.get("group_wxid") or payload.get("group_id") or group_name)
        message_type = _normalize_text(payload.get("msg-type") or payload.get("message_kind") or payload.get("type") or "text")
        text = _normalize_text(payload.get("msg-content") or payload.get("content") or payload.get("display_text"))
        received_at = _normalize_text(payload.get("time") or payload.get("send_time") or payload.get("received_at")) or None
        local_server_id = _coerce_int(payload.get("server_id") or payload.get("serverId"))
        message_key = _normalize_text(payload.get("message_key") or payload.get("messageKey") or payload.get("messageuuid"))
        image_md5 = _extract_image_md5(_normalize_text(payload.get("msg-path") or payload.get("msg_path") or ""), payload)
        msg_path = _normalize_text(payload.get("msg-path") or payload.get("msg_path"))

        metadata = {
            "local_id": local_id,
            "server_id": local_server_id,
            "message_key": message_key,
            "image_md5": image_md5,
            "group_name": group_name,
            "group_wxid": _normalize_text(payload.get("group_wxid")),
            "sender": _normalize_text(payload.get("sender")),
            "sender_wxid": _normalize_text(payload.get("sender-wxid") or payload.get("sender_wxid")),
            "message_type": message_type,
            "msg_path": msg_path,
            "source_file": str(source_path),
            "source_file_stem": source_path.stem,
        }

        raw_image_path: str | Path | None = None
        if msg_path and msg_path != "未下载":
            path = Path(msg_path).expanduser()
            if path.exists():
                raw_image_path = str(path)

        bundle = RawAssetBundle(
            message_id=message_id,
            group_id=group_id,
            source_agent=self.source_agent,
            received_at=received_at,
            raw_image_path=raw_image_path,
            ocr_json_path=None,
            message_metadata_path=str(source_path),
            text=text,
            extraction_kind=message_type or "unknown",
            registration_status="complete",
            warnings=(),
        )
        return MessageEvent(
            message_id=message_id,
            channel=self.channel,
            group_id=group_id,
            source_agent=self.source_agent,
            received_at=received_at,
            message_type=message_type,
            text=text,
            raw_asset_bundle=bundle,
            metadata=metadata,
        )

    def iter_message_events(
        self,
        *,
        group_name: str | None = None,
        source_file_stem: str | None = None,
        source_file_path: str | Path | None = None,
        limit: int | None = None,
    ) -> Iterator[MessageEvent]:
        count = 0
        for source_path, payload in self._iter_payloads(
            group_name=group_name,
            source_file_stem=source_file_stem,
            source_file_path=source_file_path,
        ):
            yield self._build_event(source_path=source_path, payload=payload)
            count += 1
            if limit is not None and count >= limit:
                break

    def read_message_events(
        self,
        *,
        group_name: str | None = None,
        limit: int | None = None,
    ) -> list[MessageEvent]:
        return list(self.iter_message_events(group_name=group_name, limit=limit))
