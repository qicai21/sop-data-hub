# WxOps Source Supervision R13 Report

## Scope
Read-only supervision of wx-ops-agent source records for SOP data hub.

## Source inputs
- Chat records: `data/chat_records/**/*.jsonl`
- Image root: `~/Documents/bussiness-artifacts/wechat_images`
- Daemon log: `data/runtime-logs/daemon-auto.log`

## Implemented contract
- Added `WxOpsSourceWatcher` in `src/ops_hub/sop/source_watcher.py`
- Emits `MessageEvent` objects from chat-record JSONL payloads
- `message_id` rule: `wx_{local_id}`
- Preserved metadata fields:
  - `local_id`
  - `server_id`
  - `message_key`
  - `image_md5`
- Added `metadata` to `MessageEvent` and preserved it through local serialization helpers

## Read-only behavior
- No changes to wx-ops-agent
- No runtime daemon changes
- No database changes
- No real sending
- No OCR or delivery execution

## Verification
- Functional test updated: `tests/functional/test_sop_data_hub_source_supervision.py`
- Targeted suite passed:
  - `test_monitoring_plan_matcher.py`
  - `test_raw_asset_bundle_registration.py`
  - `test_workflow_task_queue.py`
  - `test_lifecycle_closeout.py`
  - `test_sop_data_hub_source_supervision.py`

## Notes
- The watcher reads only local JSONL source data and keeps the implementation local-only.
- Source-file filtering supports the 2026-05 snapshot used in the acceptance test.
