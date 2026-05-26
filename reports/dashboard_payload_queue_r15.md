# R15 DashboardPayloadQueue Audit

## 1. Scope

Only ordinary-freight projects are in scope:

- 中唐特钢
- 朝阳钢铁
- 吉林金钢（锦州）

This round only follows the local payload path:

`chat_records/*.jsonl` → `WxOpsSourceWatcher` → `MessageEvent` → `Matcher` → `WorkflowTask` → `DashboardIntent` → `DashboardPayloadQueue` → `runtime/dashboard_intents/*.json`

No dashboard HTML, database, runtime daemon, report sending, 九三 logic, or wx-ops-agent writeback was touched.

## 2. New payload shape

`DashboardPayload` now includes the required fields:

- `message_id`
- `project_id`
- `group_id`
- `watch_item`
- `target_sop_node`
- `dashboard_action`
- `created_at`
- `source`
- `status`
- `payload_version`

Additional field kept for traceability:

- `reason`

Payload version used in this round:

- `r15`

## 3. Queue output

The writer now emits JSON payload files to:

- `runtime/dashboard_intents/`

Generated files in this run:

- `runtime/dashboard_intents/wx_21.json`
- `runtime/dashboard_intents/wx_2000.json`
- `runtime/dashboard_intents/wx_2.json`

Sample output shape:

```json
{
  "message_id": "wx_21",
  "project_id": "chaoyang_steel",
  "group_id": "GROUP001",
  "dashboard_action": "upsert_payload",
  "status": "ready",
  "payload_version": "r15"
}
```

## 4. Real pipeline verification

Verified end-to-end in the functional test path:

- `chat_records/*.jsonl` read by `WxOpsSourceWatcher`
- message events produced with preserved metadata
- matcher resolved ordinary-freight watch items
- workflow tasks were created
- dashboard intents were resolved as `ready`
- dashboard payload JSON files were written successfully

Verified cases:

- `wx_21` → `chaoyang_steel`
- `wx_2000` → `jilin_jingang_jinzhou`
- `wx_2` → `zhongtang_special_steel`

## 5. Source field behavior

The `source` field is populated from the message metadata path preserved by the source watcher.

In this run it points at the local chat-record JSONL path used to produce the event, for example:

- `/Users/qicai21/projects/repos/ops-data-hub/data/chat_records/铁晟业务工作群/2026-04-wx_21.jsonl`

## 6. Test verification

Passed tests:

- `tests/functional/test_sop_data_hub_source_supervision.py`
- `tests/functional/test_dashboard_alignment.py`
- `tests/functional/test_dashboard_payload_queue.py`

Result:

- `3 passed`

## 7. Notes

- This is still a local-only payload emission path.
- It does not modify `dashboard/dispatch_board.html`.
- It does not introduce database writes.
- It does not activate daemon/runtime code.
- It does not send reports or write back to wx-ops-agent.
