"""Functional tests for the raw asset bundle registration protocol.

Scope:
- local Python object registration only
- no runtime, wx-ops-agent, database, 95306, OCR execution, or report sending
"""

from ops_hub.sop.monitoring_plan_matcher import MessageEvent
from ops_hub.sop.raw_asset_bundle import bind_raw_asset_bundle, register_raw_asset_bundle


def test_register_image_message_with_raw_image_ocr_and_metadata_paths():
    event = MessageEvent(
        message_id="msg-image-1",
        channel="wechat",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T10:00:00Z",
        message_type="image",
        text="出港计划通知单",
    )

    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        raw_image_path="/tmp/sop/raw/image-001.png",
        ocr_json_path="/tmp/sop/raw/image-001.ocr.json",
        message_metadata_path="/tmp/sop/raw/image-001.meta.json",
        text=event.text,
        extraction_kind="image",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)

    assert bound_event.raw_asset_bundle == bundle
    assert bundle.is_complete
    assert bundle.registration_status == "complete"
    assert bundle.warnings == ()
    assert bundle.asset_paths == {
        "raw_image_path": "/tmp/sop/raw/image-001.png",
        "ocr_json_path": "/tmp/sop/raw/image-001.ocr.json",
        "message_metadata_path": "/tmp/sop/raw/image-001.meta.json",
    }


def test_register_text_message_with_metadata_path_only():
    event = MessageEvent(
        message_id="msg-text-1",
        channel="wechat",
        group_id="GROUP003",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T10:01:00Z",
        message_type="text",
        text="发运动态",
    )

    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        message_metadata_path="/tmp/sop/raw/text-001.meta.json",
        text=event.text,
        extraction_kind="text",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)

    assert bound_event.raw_asset_bundle == bundle
    assert bundle.is_complete
    assert bundle.registration_status == "complete"
    assert bundle.asset_paths["raw_image_path"] is None
    assert bundle.asset_paths["ocr_json_path"] is None
    assert bundle.asset_paths["message_metadata_path"] == "/tmp/sop/raw/text-001.meta.json"
    assert bound_event.raw_asset_bundle.text == "发运动态"


def test_register_missing_asset_path_yields_warning_and_incomplete_status():
    event = MessageEvent(
        message_id="msg-image-2",
        channel="wechat",
        group_id="GROUP001",
        source_agent="wx-ops-agent",
        received_at="2026-05-25T10:02:00Z",
        message_type="image",
        text="检装车通知单",
    )

    bundle = register_raw_asset_bundle(
        message_id=event.message_id,
        group_id=event.group_id or "",
        source_agent=event.source_agent,
        received_at=event.received_at,
        raw_image_path="/tmp/sop/raw/image-002.png",
        ocr_json_path=None,
        message_metadata_path="/tmp/sop/raw/image-002.meta.json",
        text=event.text,
        extraction_kind="image",
    )
    bound_event = bind_raw_asset_bundle(event, bundle)

    assert bound_event.raw_asset_bundle == bundle
    assert not bundle.is_complete
    assert bundle.registration_status == "incomplete"
    assert bundle.warnings
    assert any("missing ocr_json_path" in warning for warning in bundle.warnings)
