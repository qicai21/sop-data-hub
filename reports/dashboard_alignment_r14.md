# Dashboard Alignment R14

## 1. Scope

- Task: ordinary freight dashboard alignment.
- Projects in scope: `zhongtang_special_steel`, `chaoyang_steel`, `jilin_jingang_jinzhou`.
- Pipeline under audit: `chat_records/*.jsonl → WxOpsSourceWatcher → MessageEvent → Matcher → WorkflowTask → DashboardIntent`.
- Output: dashboard payload only.
- Explicit non-goals: no dashboard HTML writes, no database writes, no runtime changes, no report sending, no 九三 logic.

## 2. Real input structure

### 2.1 微信消息原始结构

The real source file format under `wx-ops-agent/data/chat_records/**/*.jsonl` is JSONL.
Observed payload keys in the raw records:

- `msg-content`
- `msg-path`
- `msg-type`
- `remark`
- `sender`
- `sender-wxid`
- `seq`
- `time`

Typical examples from the live records:

- text message: `msg-type=text`, `msg-content=...`, `msg-path=''`
- image message: `msg-type=image`, `msg-content=''`, `msg-path` may be `未下载` or a real file path

### 2.2 图片保存位置

Observed image cache root:

- `~/Documents/bussiness-artifacts/wechat_images`

Observed on-disk example:

- `/Users/qicai21/Documents/bussiness-artifacts/wechat_images/202604/中唐特钢发运群/2026-04/2_d18b69f1dec3399f1288cf8bc8f4c366.jpg`

Current rule inferred from the watcher:

- the bundle resolves `msg-path`
- if the path exists, it is preserved as `raw_image_path`
- if the record says `未下载`, the bundle keeps the image path empty

### 2.3 JSON 保存位置

The actual message JSONL source is stored under:

- `/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records/<group_name>/<YYYY-MM>.jsonl`

This is the real source used in the audit, for example:

- `/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records/铁晟业务工作群/2026-04.jsonl`
- `/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records/中唐特钢发运群/2026-05.jsonl`

### 2.4 消息 metadata

`WxOpsSourceWatcher` preserves the following metadata on `MessageEvent.metadata`:

- `local_id`
- `server_id`
- `message_key`
- `image_md5`
- `group_name`
- `group_wxid`
- `sender`
- `sender_wxid`
- `message_type`
- `msg_path`
- `source_file`
- `source_file_stem`

### 2.5 message_id 来源

`message_id` is generated as:

- `wx_{local_id}`
- if `local_id` is absent, the watcher falls back to `seq`

Example:

- `seq=2000` → `message_id=wx_2000`
- `seq=2` → `message_id=wx_2`

## 3. 当前运行方式

The current source supervision path is read-only and local:

- scan existing JSONL files
- build `MessageEvent`
- match against the compiled plan
- create `WorkflowTask`
- derive `DashboardIntent`

No live WeChat runtime, no database, and no dashboard HTML write are part of this alignment step.

Operational note:

- the daemon log shows an auto poll cycle every 60s
- the process is currently running as a local poller, not as a write-back service

## 4. 当前输出目录

Observed output-related paths:

- image cache / output asset root: `~/Documents/bussiness-artifacts/wechat_images`
- audit/report output: `reports/`
- runtime logs: `data/runtime-logs/`

Important caveat:

- `WxOpsSourceWatcher`'s default root resolution points to `/Users/qicai21/projects/wx-ops-agent/...`
- the real data used for this audit is under `/Users/qicai21/projects/repos/wx-ops-agent/...`
- the functional tests were run by pointing the watcher at the repo copy explicitly

## 5. 是否已有日志

Yes.

Verified log file:

- `/Users/qicai21/projects/repos/wx-ops-agent/data/runtime-logs/daemon-auto.log`

Observed first lines:

- `2026-05-09 18:10:15 [INFO] [Tracker] Using authoritative tasks derived from ops-data-hub ProjectSOPs`
- `2026-05-09 18:10:15 [INFO] WeChat Ops Agent Daemon started [Cycle interval: 60s, UI mode: auto]`
- `2026-05-09 18:10:15 [INFO] [Daemon] Starting poll cycle for 3 groups: ['铁晟业务工作群', '数据单发群-[GROUP013]', '中唐特钢发运群']`

## 6. Dashboard payload verification

Verified with real messages from chat_records:

- `wx_21` → `chaoyang_steel`
- `wx_2000` → `jilin_jingang_jinzhou`
- `wx_2` → `zhongtang_special_steel`

For each case, the alignment produced a `DashboardIntent` with:

- `project_id`
- `message_id`
- `watch_item`
- `target_sop_node`
- `status=ready`
- `dashboard_action=upsert_payload`

## 7. Notes

- This round only generates dashboard payloads.
- It does not touch `dashboard/dispatch_board.html`.
- It does not write to the database.
- It does not send reports.
- It does not activate any 九三 logic.
