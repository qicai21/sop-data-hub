# R30: 吉林金钢 canonical SOP implementation gap audit

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**SOP Commit:** `734b800`
**SOP File:** `config/project_sops/jilin_jingang.yaml`
**SOP Version:** `v0.2`
**Audit Type:** Read-only, no code changes

---

## Git Log

```
734b800 docs: update canonical jilin jingang SOP yaml
094da92 R28: 吉林金钢 SOP 制单后链路核查 — WAIT_95306_CONFIRM is the SOP endpoint
5f9ab7b R27: canonical SOP source migration — config/project_sops/
23fd8c1 R26: live SOP runtime compiler — SopWatcher with hot-reload
a2d8f07 R24: real ordinary freight acceptance audit
```

**SOP SHA256:** `34e4828fb38448c51de18569626228eadef88a5cc72b60d521895ba7ae397fe8`

---

## 0. Critical Discovery: YAML Parsing Incompatibility

The new SOP v0.2 uses a **nested structure**:

```yaml
project:
  id: jilin_jingang_jinzhou
  name: ...
```

But `load_project_sop()` in `models/project_sop.py` reads **flat keys**:

```python
project_id=data.get("project_id", "")    # reads '', not 'jilin_jingang_jinzhou'
project_name=data.get("project_name", "")  # reads ''
```

**Result:** `jilin_jingang_jinzhou` does NOT appear in `sop_runtime.loaded_projects`. Instead, an empty-string project `""` appears.

This means the SOP is **loaded structurally but functionally invisible** — the YAML exists, the watcher hot-reloads it, but the project_id is lost during parsing. All downstream monitoring plan compilation, message routing, and workflow tasks will not carry the correct project identity.

**Severity: P0 — blocks all automation.**

---

## Live Service Status

```json
{
  "sop_runtime": {
    "loaded_projects": ["chaoyang_steel", "", "jiusan", "zhongtang_special_steel"],
    "last_reload": "2026-05-28T06:06:21Z",
    "sop_hash": "12d10ac88920bf70",
    "sop_dir": "/Users/qicai21/projects/repos/sop-data-hub/config/project_sops",
    "source_of_truth": "git",
    "file_hashes": {
      "jilin_jingang.yaml": "34e4828fb38448c5"
    }
  }
}
```

**Verification results:**
- `sop_runtime.source_of_truth` = `"git"` ✅
- `sop_dir` = `config/project_sops` ✅
- `loaded_projects` contains `jilin_jingang_jinzhou`? **NO** ❌ — instead contains empty string `""`

---

## 1. release_notice_flow Audit

| SOP Node | Status | File | Function | Table |
|----------|--------|------|----------|-------|
| `detect_release_notice` | 部分实现 | `monitoring_plan_matcher.py` | `match_message_event()`, `_fallback_alignment_match()` | — |
| `identify_project` | 部分实现 | `monitoring_plan_matcher.py` | `_fallback_alignment_match()` (GROUP001 + "四平" → jilin_jingang_jinzhou) | — |
| `find_existing_release_batch` | 部分实现 | `agent.py:315` | `ingest_release_batch()` — implicit via `ON CONFLICT(batch_key)` upsert | `release_batches` |
| `create_or_update_release_batch` | 已实现 | `agent.py:315` | `ingest_release_batch()` | `release_batches` |
| `update_existing_release_batch` | 已实现 | `agent.py:315` | `ingest_release_batch()` — `ON CONFLICT` DO UPDATE | `release_batches` |

### Details

- **detect_release_notice / classify_message:** `monitoring_plan_matcher` handles message matching via `match_message_event()` with document_type matching for images and text_pattern matching for text. Image messages (release_notice_image) route through `_match_document_item`. The `_fallback_alignment_match` provides keyword-based routing ("四平" → jilin_jingang_jinzhou).

- **identify_project:** The fallback matcher identifies `jilin_jingang_jinzhou` when GROUP001 messages contain "四平" tokens. No keyword matching for GROUP005/GROUP013 in current code.

- **find_existing_release_batch:** The SOP specifies matching by `order_identifier` + `contract_no` + `ship_name` + `destination`. Current implementation uses `batch_key` (computed hash) for uniqueness. The `order_identifier` field does NOT exist in the `release_batches` table schema.

- **Missing:** No `extract_release_notice_json` function exists. The release notice JSON extraction happens through OCR → `runner.py` pipeline, but there is no dedicated `release_notice_json` output node.

### Gap
The `release_batches` table lacks `order_identifier` field — SOP requires matching on this field.

---

## 2. freight_detail_flow Audit

| SOP Node | Status | File | Function | Table |
|----------|--------|------|----------|-------|
| 从 GROUP005 / GROUP013 识别货运信息 | 未实现 | — | — | — |
| 提取 order_identifier | 未实现 | — | — | — |
| 提取 contract_no | 未实现 | — | — | — |
| 提取 cargo_name_detail | 未实现 | — | — | — |
| enrich_release_batch | 未实现 | — | — | — |

### Details

The SOP defines GROUP005 and GROUP013 as sources for `freight_detail_text` messages. Current code has:

- **No GROUP005/GROUP013 routing** in `monitoring_plan_matcher._fallback_alignment_match`. Only GROUP001 is handled for jilin_jingang_jinzhou.
- **No freight_detail_text message type** defined in the monitoring plan compiler.
- **No field extraction** pipeline for `order_identifier`, `contract_no`, or `cargo_name_detail`.
- **No `enrich_release_batch`** function exists. The `release_batches` table lacks `order_identifier` and `cargo_name_detail` columns.

### Gap
Entire freight_detail_flow is unimplemented. The `release_batches` table cannot store `order_identifier` or `cargo_name_detail`.

---

## 3. departure_flow Audit

| SOP Node | Status | File | Function | Table |
|----------|--------|------|----------|-------|
| 识别"6道，四平铁，46车"发运文本 | 未实现 | — | — | — |
| 提取 message_time | 未实现 | — | — | — |
| 提取 destination | 未实现 | — | — | — |
| 提取 car_count | 未实现 | — | — | — |
| 构造 95306 查询窗口 (msg_time ±60m) | 未实现 | — | — | — |
| 查询 95306 | 未实现 | — | — | — |
| 提取 wagon_no | 未实现 | — | `gen_jljg_excel.py` reads `wagon_shipments.car_no` (pre-loaded) | `wagon_shipments` |
| 提取 container_no | 未实现 | — | — | — |
| 提取 waybill_no | 未实现 | — | — | — |
| 去重 | 未实现 | — | — | — |
| 绑定 release_batch | 未实现 | — | `gen_jljg_excel.py` reads via `departure_id` FK | `wagon_shipments` |
| 写 wagon_shipments | 部分实现 | `gen_jljg_excel.py:27` | Reads from existing `wagon_shipments` table | `wagon_shipments` |
| 生成 departure_excel | 部分实现 | `gen_jljg_excel.py` | Standalone script, hardcoded contract/order/ship values | — |
| 生成 factory_transport_json | 未实现 | `gen_jljg_excel.py:68-83` | Print-only preview, hardcoded | — |
| dry-run receiver system (HTTP 200) | 未实现 | — | — | — |
| 测试阶段 Excel 发送给郭东北 | 未实现 | — | — | — |
| JSON 通过 Telegram dry-run 发送 | 未实现 | — | — | — |

### Details

- **`gen_jljg_excel.py`**: Standalone script with hardcoded values:
  ```python
  contract_no = 'JGCG-SFY-HTNK20260501'    # hardcoded
  order_identifier = 'CGR20260518174420'     # hardcoded
  ship_name = '长航滨海'                      # hardcoded
  container_no = '待补-需从95306API提取箱号'    # placeholder
  ```
  No departure text parsing. No 95306 query. Reads from pre-existing `wagon_shipments` rows.

- **`departure_records` table** exists with `batch_id`, `ship_name`, `cargo_name`, `plan_id`, `contract_no`, `departure_date`, `wagon_count`, `car_nos`, `doc_time`. But no `destination`, `car_count` from message text.

- **`wagon_shipments` table** exists with `car_no`, `cargo_name`, `ticketed_at`, `departed_at`, `arrived_at`, but **lacks**: `container_no`, `waybill_no`.

- **No 95306 query integration** — the current reconciler (`inspection_95306_reconciler.py`) queries from `shipments` table in a separate 95306 DB by `car_no` + destination + cargo aliases, not from message_time window.

### Gap
Almost the entire departure_flow is unimplemented. The existing `gen_jljg_excel.py` is a prototype with hardcoded values and no automation.

---

## 4. tracking_flow Audit

| SOP Node | Status | File | Function | Table |
|----------|--------|------|----------|-------|
| poll_shipment_snapshots | 未实现 | — | — | — |
| 识别 95306 状态：发车/到站/交付 | 未实现 | — | — | — |
| update_dashboard_state | 未实现 | — | `dispatch_board.py` renders static HTML, no state write-back | — |
| update_wagon_arrival_status | 未实现 | — | — | — |
| update_dispatch_status | 已实现(部分) | `agent.py` | `update_release_dispatch_status()` — manual CLI only | `release_batches` |
| mark_confirmed_received | 未实现 | — | — | — |
| close_dashboard_state | 未实现 | — | — | — |

### Why 长航滨海 remains in_progress

**Root cause:** The SOP v0.2 now defines `tracking_flow` with explicit states: 发车 → 到站 → 交付 → confirmed_received → closed. However:

1. **No polling mechanism exists.** There is no process that periodically queries 95306 for shipment status updates. The current live service (`run_live_service.py`) only polls `wx-ops-agent/data/chat_records` for new messages — it does not poll 95306.

2. **No status tracking pipeline.** Even if 95306 data is available, there is no code that:
   - Reads `shipment_release_batch_matches` to find tracked wagons
   - Checks `latest_stage_name` for 发车/到站/交付
   - Updates `wagon_shipments` (arrived_at, departed_at)
   - Updates `release_batches.dispatch_status`

3. **Missing DB fields:** The `release_batches` table has `dispatch_status` (in_progress/completed/suspended/cancelled) but **lacks** intermediate states: `arrival_status`, `delivered_at`, `confirmed_received_at`. The `wagon_shipments` table has `departed_at` and `arrived_at` but no `delivered_at` or `confirmed_received`.

### What's needed to reach confirmed_received

1. **P0:** A `ShipmentSnapshotPoller` that queries 95306 periodically
2. **P0:** Status mapping logic: 95306 `latest_stage_name` → SOP states
3. **P0:** Database columns: `wagon_shipments.delivered_at`, `release_batches.arrival_status`, `release_batches.confirmed_received_at`
4. **P1:** `update_dashboard_state` / `close_dashboard_state` logic
5. **P1:** `mark_confirmed_received` — trigger mechanism (auto or manual)

---

## 5. runtime.task_resolver Audit

| Task Type | Status | File | Function |
|-----------|--------|------|----------|
| `excel_generation` | 未实现 | — | — |
| `json_generation` | 未实现 | — | — |
| `telegram_delivery` | 未实现 | — | — |
| `http_delivery` | 未实现 | — | — |

### Details

- **No unified TaskResolver exists.** The SOP YAML (lines 311-319) defines a `runtime.task_resolver` section with 4 task types, but no implementation exists.
- **Scattered implementations:**
  - `report_intent.py` — Resolves report intent (template path, recipient) for workflow tasks. Local-only, does not actually generate or deliver.
  - `delivery_result.py` — Simulates delivery results. Uses `simulate_delivery_result()` — explicitly "local functional tests".
  - `gen_jljg_excel.py` — Standalone Excel generation script with hardcoded values.
  - `report_tasks` table — Exists in DB with `release_batch_id`, `project_id`, `report_path`, `wagon_count`, `target_type`, `target_name`, `status`. But no code writes to it for jilin_jingang.
- **Missing:** No actual Excel generation service, no JSON generation service, no Telegram sender, no HTTP client for receiver system.

---

## 6. Database Mapping Audit

### release_batches table

| SOP Entity | DB Column | Exists? | Notes |
|------------|-----------|:------:|-------|
| id | id | ✅ | |
| ship_name | ship_name | ✅ | |
| destination | destination_station | ✅ | |
| quantity | batch_quantity | ✅ | |
| cargo_name | cargo_name | ✅ | |
| order_identifier | — | ❌ | Missing entirely |
| contract_no | contract_no | ✅ | |
| cargo_name_detail | — | ❌ | Missing entirely |
| confirmed_received | — | ❌ | No tracking state fields |
| dispatch_status | dispatch_status | 部分 | Only in_progress/completed/suspended/cancelled |
| arrival_status | — | ❌ | |
| delivered_at | — | ❌ | |
| confirmed_received_at | — | ❌ | |

### wagon_shipments table

| SOP Entity | DB Column | Exists? | Notes |
|------------|-----------|:------:|-------|
| wagon_no | car_no | ✅ | |
| waybill_no | — | ❌ | Missing |
| container_no | — | ❌ | Missing |
| release_batch_id | batch_id | ✅ | FK to release_batches |
| departed_at | departed_at | ✅ | |
| arrived_at | arrived_at | ✅ | |
| delivered_at | — | ❌ | |
| confirmed_received | — | ❌ | |

### shipment_release_batch_matches table

Created and managed by `inspection_95306_reconciler.py` in the **95306 SQLite DB** (not in `sop_agent.db`). Schema: `id`, `release_batch_id`, `shipment_ydid`, `shipment_car_no`, `ticketed_at`, `status_code`, `status_name`, `latest_stage_name`, `latest_event_time`, etc. This table is the formal linkage between release batches and 95306 shipments.

---

## 7. Gap Matrix

### P0 — Main chain blocked

| # | SOP Node | Gap | Suggested Fix |
|---|----------|-----|---------------|
| P0-1 | YAML parser | `load_project_sop()` reads flat `project_id` key; SOP v0.2 uses nested `project.id` | Fix parser to support nested structure, or add flat aliases to YAML |
| P0-2 | freight_detail_flow (entire) | No GROUP005/GROUP013 routing, no field extraction, no enrich_release_batch | Implement freight_detail_text message handler with field extraction |
| P0-3 | departure_flow — 95306 query | No message_time-based 95306 query window; no wagon_no/container_no/waybill_no extraction | Implement departure text parser → 95306 query → field extraction pipeline |
| P0-4 | departure_flow — write | No automated wagon_shipments write from 95306 data; no dedup | Implement write pipeline with (wagon_no, waybill_no, release_batch_id) uniqueness |
| P0-5 | tracking_flow — polling | No 95306 status polling mechanism | Implement ShipmentSnapshotPoller |
| P0-6 | DB — missing columns | `release_batches` lacks `order_identifier`, `cargo_name_detail`; `wagon_shipments` lacks `container_no`, `waybill_no`, `delivered_at` | Add columns via migration |
| P0-7 | DB — tracking columns | No `arrival_status`, `delivered_at`, `confirmed_received_at` in `release_batches` | Add columns via migration |
| P0-8 | task_resolver (entire) | No excel_generation, json_generation, telegram_delivery, http_delivery | Implement TaskResolver service |

### P1 — Automation gap, manual fallback available

| # | SOP Node | Gap | Suggested Fix |
|---|----------|-----|---------------|
| P1-1 | release_notice — order_identifier matching | `find_existing_release_batch` uses `batch_key` hash, not `order_identifier` + `contract_no` | Add `order_identifier` index and use composite match |
| P1-2 | departure_flow — Excel generation | `gen_jljg_excel.py` has hardcoded values | Make data-driven from DB |
| P1-3 | departure_flow — factory JSON | No JSON generation pipeline | Implement from wagon_shipments data |
| P1-4 | departure_flow — dry-run | No HTTP receiver dry-run | Implement HTTP client with configurable endpoint |
| P1-5 | departure_flow — delivery | No Telegram/Excel delivery | Implement delivery service (report_tasks table ready) |
| P1-6 | tracking_flow — status mapping | No 95306 status → SOP state mapping | Implement status mapper: 发车→dispatched, 到站→arrived, 交付→delivered |
| P1-7 | tracking_flow — dashboard update | `dispatch_board.py` renders static HTML only | Add state write-back to release_batches |
| P1-8 | tracking_flow — confirmed_received | No mark_confirmed_received logic | Implement auto/manual confirmation trigger |

### P2 — Report / UX optimization

| # | SOP Node | Gap | Suggested Fix |
|---|----------|-----|---------------|
| P2-1 | release_notice — extract_release_notice_json | No dedicated extraction function | Formalize OCR→JSON pipeline output |
| P2-2 | departure_flow — optional_ship_name | Not extracted from departure text | Add ship_name extraction for multi-batch matching |
| P2-3 | tracking_flow — close_dashboard_state | Dashboard lifecycle closeout not implemented | Add close transition when all wagons confirmed_received |
| P2-4 | exceptions — ocr_incomplete | Exception handling exists in `runner.py` but not SOP-aware | Add SOP exception routing |
| P2-5 | exceptions — shipment_count_mismatch | No manual confirmation flow for count mismatches | Add confirmation prompt |

---

## 8. Architecture Observations

### What works well
- **Release batch ingestion:** `BusinessDataAgent.ingest_release_batch()` handles OCR→JSON→DB with idempotent upsert.
- **Inspection reconciler:** `reconcile_inspection_shipments()` provides formal plan/commit workflow for 95306 matching.
- **Message matching:** `monitoring_plan_matcher` + `fallback_alignment_match` provide keyword-based project routing.
- **Live service:** `run_live_service.py` correctly polls chat_records, emits events, and hot-reloads SOPs.
- **Database foundation:** Core tables (`release_batches`, `wagon_shipments`, `departure_records`) exist with proper FKs.

### Critical architecture gaps
1. **SOP v0.2 is structurally incompatible** with the YAML parser — blocks everything downstream.
2. **No message-text parser** for departure text ("6道，四平铁，46车") — blocks the entire departure_flow.
3. **No 95306 integration** beyond the inspection reconciler — tracking, polling, and status updates are entirely missing.
4. **No TaskResolver** — the SOP defines 4 task types but zero implementation exists.
5. **No delivery infrastructure** — Excel, JSON, Telegram, HTTP delivery are all stubs or hardcoded scripts.

---

## 9. Next Round Recommendations (Do Not Execute)

1. **Fix YAML parser** to handle nested `project.id` structure — unblocks everything.
2. **Implement freight_detail_flow** — simplest flow to add, enables data enrichment.
3. **Add DB columns** — `order_identifier`, `cargo_name_detail`, `container_no`, `waybill_no`, tracking fields.
4. **Implement departure text parser** — regex-based extraction of message_time/destination/car_count.
5. **Implement 95306 query window** — build from message_time ±60m.
6. **Implement TaskResolver** — start with `excel_generation` (refactor `gen_jljg_excel.py`) and `json_generation`.
7. **Implement tracking polling** — periodic 95306 status query with state mapping.

**Priority order:** P0-1 (YAML parser) → P0-6 (DB columns) → P0-2 (freight_detail) → P0-3/P0-4 (departure_flow) → P0-5 (tracking) → P0-8 (TaskResolver) → P1 items → P2 items.

---

**Audit complete. No code changed. No SOP modified. No live service restarted.**
