"""Raw asset bundle registration protocol for local SOP functional tests.

This module stays local-only:
- record raw image / OCR JSON / metadata paths;
- bind the bundle to MessageEvent;
- surface incomplete registrations with warnings instead of crashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawAssetBundle:
    message_id: str
    group_id: str
    source_agent: str
    received_at: str | None = None
    raw_image_path: str | Path | None = None
    ocr_json_path: str | Path | None = None
    message_metadata_path: str | Path | None = None
    text: str = ""
    extraction_kind: str = "unknown"
    registration_status: str = "incomplete"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return self.registration_status == "complete"

    @property
    def asset_paths(self) -> dict[str, str | None]:
        return {
            "raw_image_path": str(self.raw_image_path) if self.raw_image_path is not None else None,
            "ocr_json_path": str(self.ocr_json_path) if self.ocr_json_path is not None else None,
            "message_metadata_path": str(self.message_metadata_path) if self.message_metadata_path is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "source_agent": self.source_agent,
            "received_at": self.received_at,
            "raw_image_path": self.asset_paths["raw_image_path"],
            "ocr_json_path": self.asset_paths["ocr_json_path"],
            "message_metadata_path": self.asset_paths["message_metadata_path"],
            "text": self.text,
            "extraction_kind": self.extraction_kind,
            "registration_status": self.registration_status,
            "warnings": list(self.warnings),
        }


def register_raw_asset_bundle(
    *,
    message_id: str,
    group_id: str,
    source_agent: str,
    received_at: str | None = None,
    raw_image_path: str | Path | None = None,
    ocr_json_path: str | Path | None = None,
    message_metadata_path: str | Path | None = None,
    text: str = "",
    extraction_kind: str = "unknown",
) -> RawAssetBundle:
    """Create a local asset bundle registration result.

    The protocol is intentionally permissive: missing paths yield warnings and
    `registration_status='incomplete'` instead of exceptions.
    """

    warnings: list[str] = []

    if not message_metadata_path:
        warnings.append("missing message_metadata_path")

    if extraction_kind == "image":
        if not raw_image_path:
            warnings.append("missing raw_image_path for image message")
        if not ocr_json_path:
            warnings.append("missing ocr_json_path for image message")
    elif extraction_kind == "ocr":
        if not ocr_json_path:
            warnings.append("missing ocr_json_path for OCR registration")
    elif extraction_kind == "text":
        pass
    else:
        if not any((raw_image_path, ocr_json_path, message_metadata_path)):
            warnings.append("no asset paths registered")

    registration_status = "complete" if not warnings else "incomplete"
    return RawAssetBundle(
        message_id=message_id,
        group_id=group_id,
        source_agent=source_agent,
        received_at=received_at,
        raw_image_path=raw_image_path,
        ocr_json_path=ocr_json_path,
        message_metadata_path=message_metadata_path,
        text=text,
        extraction_kind=extraction_kind,
        registration_status=registration_status,
        warnings=tuple(warnings),
    )


def bind_raw_asset_bundle(event: Any, bundle: RawAssetBundle) -> Any:
    """Return a copy of the event with the bundle attached."""

    return replace(event, raw_asset_bundle=bundle)
