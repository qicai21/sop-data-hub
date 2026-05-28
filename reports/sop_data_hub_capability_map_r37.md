# R37: SOP Data Hub capability map and executor architecture audit

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Audit Type:** 只读、归档、规划 — 不新增功能

---

## 状态确认

```
live_service --status: source_of_truth=git, loaded_projects=4, jilin_jingang_jinzhou ✅
pytest tests/functional -v: 123 passed, 0 failed
```

---

## Part A: Capability Map

### 1. sop-compiler（SOP 编译器）

| 能力 | 已实现 | 文件 | 输入 | 输出 | 状态 | 缺口 | 优先级 |
|------|:--:|------|------|------|:--:|------|:--:|
| SOP YAML loader | ✅ | `models/project_sop.py` (load_project_sop) | `config/project_sops/*.yaml` | `ProjectSOP` dataclass | implemented | — | — |
| SOPWatcher (hot-reload) | ✅ | `sop/sop_watcher.py` | sops dir mtime change | reloaded monitoring_plan | implemented | — | — |
| SopMonitoringPlanCompiler | ✅ | `sop/monitoring_plan_compiler.py` | compiler_input | `wechat_monitoring_plan` | implemented | — | — |
| SOPTaskCompiler | ✅ | `sop/sop_task_compiler.py` | SOP YAML (flows section) | `ExecutableTaskPlan` (28 tasks) | implemented | 仅 jilin_jingang 有 flows 段；chaoyang/jiusan/zhongtang 的 task 数为 0 | P1 |
| TaskExecutionRegistry | ✅ | `sop/task_execution_registry.py` | `MessageEvent` + `ExecutableTaskPlan` | `TaskExecutionTrace` | implemented | — | — |
| sop_runtime status | ✅ | `scripts/run_live_service.py` | — | JSON status | implemented | — | — |
| sop_task_runtime status | ✅ | `scripts/run_live_service.py` | — | task plan summaries | implemented | — | — |
| sop_task_trace_runtime status | ✅ | `scripts/run_live_service.py` | — | trace_dir stats | implemented | — | — |
| Text patterns → compiler | ✅ | `sop_watcher.py` + `project_sop.py` | `RoutingRule.text_patterns` | `monitoring_plan` entries | R32 | — | — |
| Executor status registry | ✅ | `sop/task_execution_registry.py` | action name | executor_status + evidence_file | implemented | 只有 8 个 action 注册 | P1 |
| Multi-project compilation | 部分 | — | — | — | — | chaoyang/jiusan/zhongtang YAML 无 `flows` 段，无法编译 task plan | P1 |

**结论：sop-compiler 是当前最成熟的模块。** SOP YAML → monitoring_plan → task_plan → task_trace 链路完整，仅缺其他项目的 YAML 标准化。

---

### 2. wx-tracker（微信消息跟踪）

| 能力 | 已实现 | 文件 | 输入 | 输出 | 状态 | 缺口 | 优先级 |
|------|:--:|------|------|------|:--:|------|:--:|
| wx-ops-agent chat_records 输入 | ✅ | `sop/source_watcher.py` | `**/*.jsonl` | `MessageEvent` | implemented | — | — |
| MessageEvent 模型 | ✅ | `sop/monitoring_plan_matcher.py` | raw chat data | structured event | implemented | — | — |
| RawAssetBundle | ✅ | `sop/raw_asset_bundle.py` | image/ocr/metadata paths | asset bundle | implemented | — | — |
| monitoring_plan_matcher | ✅ | `sop/monitoring_plan_matcher.py` | `MessageEvent` + `monitoring_plan` | `MessageMatchResult` | implemented | — | — |
| fallback_alignment_match | ✅ | `sop/monitoring_plan_matcher.py` | event text | project routing | implemented | 只处理 GROUP001/003 | P1 |
| GROUP005 图像匹配 | ✅ | `sop/monitoring_plan_matcher.py` | image event | `detect_release_notice` | R32 | — | — |
| GROUP013 图像匹配 | ✅ | `sop/monitoring_plan_matcher.py` | image event | `detect_release_notice` | R32 | — | — |
| freight_detail_text 匹配 | ✅ | `sop/monitoring_plan_matcher.py` | text with keywords | `enrich_release_batch` | R32 | — | — |
| departure text 检测 | ✅ | `sop/departure_text_parser.py` | departure text | `DepartureCandidate` | R33 | — | — |
| runtime/events 归档 | ✅ | `scripts/run_live_service.py` | events | JSON snapshot | implemented | — | — |
| runtime/task_traces 归档 | ✅ | `sop/task_execution_registry.py` | traces | JSON files | R35 | — | — |
| 图片与 JSON 归档 | 部分 | `runner.py` | classified images | `projects/.../images/`, `projects/.../json/` | implemented | 需 OCR 管线触发 | — |
| GROUP005/013 消息 fallback | ❌ | — | — | — | missing | `_fallback_alignment_match` 无 GROUP005/013 分支 | P1 |
| 消息去重 | ❌ | — | — | — | missing | SOP 定义 `unique_key=(group_id, seq)`，无实现 | P1 |

**结论：wx-tracker 具备核心的监听+匹配能力。** GROUP001 的路由完整，GROUP005/013 的 monitoring plan 匹配通过 R32 实现。缺口在 fallback 和去重。

---

### 3. 95306-tracker（95306 跟踪）

| 能力 | 已实现 | 文件 | 输入 | 输出 | 状态 | 缺口 | 优先级 |
|------|:--:|------|------|------|:--:|------|:--:|
| 95306 DB 路径 | ✅ | `shipment_status_sync.py` | `~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3` | shipments table | implemented | — | — |
| shipment_snapshots 查询 | ✅ | `shipment_status_sync.py` | car_no + destination | 95306 status row | implemented | — | — |
| shipment_status_sync (manual) | ✅ | `scripts/sync_shipment_status_from_95306.py` | ship_name | `SyncResult` | R36 | — | — |
| departed_at 同步 | ✅ | `shipment_status_sync.py` | 95306 departed_at | `wagon_shipments.departed_at` | R36 | — | — |
| arrived_at 同步 | ✅ | `shipment_status_sync.py` | 95306 arrived_at | `wagon_shipments.arrived_at` | R36 | — | — |
| delivered_at 同步 | ❌ | — | 95306 delivered_at | `wagon_shipments.delivered_at` | missing | `delivered_at` 列不存在 | P0 |
| dispatch_status 推进 | 部分 | `shipment_status_sync.py` | batch delivery status | `release_batches.dispatch_status` | R36 | 只设 delivered，不推进到 confirmed_received | P0 |
| confirmed_received 推进 | ❌ | — | — | — | missing | 无 auto/manual 确认逻辑 | P0 |
| 定时轮询任务 | ❌ | — | — | — | missing | 无 cron job；需手动执行 CLI | P0 |
| query_window (msg_time ±60m) | ❌ | — | — | — | missing | SOP departure_flow 要求，当前 sync 不用窗口查询 | P1 |
| waybill_no / container_no 提取 | ❌ | — | — | — | missing | `wagon_shipments` 缺这两列 | P0 |

**结论：95306-tracker 具备手动同步能力，但不具备自动化能力。** CLI 工具可 single-shot sync，但无定时轮询、无 confirmed_received、缺 tracking 字段。

---

### 4. data-processing executors（数据处理）

| 能力 | 已实现 | 文件 | 输入 | 输出 | 状态 | 缺口 | 优先级 |
|------|:--:|------|------|------|:--:|------|:--:|
| OCR JSON | ✅ | `runner.py` (legacy pipeline) | image | extracted JSON | legacy | 不是 SOP-aware | P2 |
| release_notice_json | ✅ | `runner.py` → `agent.py` | OCR JSON | `release_batches` row | implemented | — | — |
| departure_text_parser | ✅ | `sop/departure_text_parser.py` | text | `DepartureCandidate` | R33 | — | — |
| freight_detail_text extraction | ❌ | — | text with keywords | extracted fields | missing | matcher 工作，但无字段提取逻辑 | P0 |
| Excel generation | prototype | `scripts/gen_jljg_excel.py` | wagon_shipments (DB) | .xlsx file | prototype | 硬编码 ship_name/contract_no/order_id/container_no | P1 |
| factory JSON generation | prototype | `scripts/gen_jljg_excel.py:68-83` | wagon_shipments | print-only JSON preview | prototype | 硬编码，非数据驱动 | P1 |
| PDF generation | ❌ | — | — | — | missing | 无实现 | P2 |
| classification pipeline | ✅ | `src/ops_hub/classifier/`, `src/ops_hub/pipeline/` | image | classified category | legacy | 不通过 SOP 触发 | P2 |
| inspection slip processing | ✅ | `engines/inspection_slip.py` | image JSON | car/weight data | legacy | 不通过 SOP 触发 | P2 |
| handwritten list processing | ✅ | `engines/handwritten_list.py` | image JSON | car numbers | legacy | 不通过 SOP 触发 | P2 |

**结论：data-processing 缺乏 SOP 感知的执行器。** departure 解析是新写的唯一 SOP-aware executor。OCR/classification 管线是 legacy，不通过 SOP 路由。

---

### 5. db executors（数据库操作）

| 能力 | 已实现 | 文件 | 表 | 状态 | 缺口 | 优先级 |
|------|:--:|------|-----|:--:|------|:--:|
| release_batches CRUD | ✅ | `data_agent/agent.py` | `release_batches` | implemented | — | — |
| wagon_shipments read | ✅ | `gen_jljg_excel.py`, `shipment_status_sync.py` | `wagon_shipments` | implemented | — | — |
| wagon_shipments write (departed_at/arrived_at) | ✅ | `shipment_status_sync.py` | `wagon_shipments` | R36 | — | — |
| wagon_shipments write (new records) | ❌ | — | `wagon_shipments` | missing | 无代码自动创建 wagon_shipment 行 | P0 |
| shipment_release_batch_matches | ✅ | `matching/inspection_95306_reconciler.py` | 95306 DB | implemented | 只在 reconciler 中管理 | — |
| dashboard_state (read) | ✅ | `dispatch_board.py` | `release_batches` | implemented | 只读，无反写 | — |
| dashboard_state (write) | ❌ | — | — | missing | 无 `update_dashboard_state` 实现 | P0 |
| batch 状态推进 | 部分 | `shipment_status_sync.py` | `release_batches` | R36 | 只设 delivered，不推进到 confirmed_received | P0 |
| 幂等规则 | 部分 | `agent.py` (batch_key UNIQUE) | `release_batches` | implemented | 仅 release_batch 去重；消息去重全缺 | P1 |
| contracts | ✅ | `data_agent/db.py` | `contracts` | implemented | — | — |
| inspection_ingestion_candidates | ✅ | `data_agent/db.py` | `inspection_ingestion_candidates` | implemented | — | — |
| report_tasks | ✅ | `data_agent/db.py` | `report_tasks` | implemented | 表存在，但 jilin_jingang 无代码写入 | P1 |
| departure_records | ✅ | `data_agent/db.py` | `departure_records` | implemented | 表存在，但无 SOP-aware 代码写入 | P1 |
| Schema 缺口 | | | | | |
| — order_identifier | ❌ | `release_batches` | missing | P0 |
| — cargo_name_detail | ❌ | `release_batches` | missing | P0 |
| — container_no | ❌ | `wagon_shipments` | missing | P0 |
| — waybill_no | ❌ | `wagon_shipments` | missing | P0 |
| — delivered_at | ❌ | `wagon_shipments` | missing | P0 |
| — confirmed_received_at | ❌ | `release_batches` | missing | P0 |

**结论：db 层有良好的数据基础，但 SOP 驱动的写入能力不足。** 6 个 schema 缺口 + 多个缺少 executor 的 DB 操作。

---

### 6. report-sender executors（报告发送）

| 能力 | 已实现 | 文件 | 状态 | 缺口 | 优先级 |
|------|:--:|------|:--:|------|:--:|
| WeChat 发送 | ❌ | — | missing | 无实现 | P1 |
| Telegram dry-run | ❌ | `delivery_result.py` (simulate only) | dry_run_only | 无真实发送 | P1 |
| 工厂系统 JSON 上传 | ❌ | — | missing | HTTP client 不存在 | P1 |
| response_code=200 检查 | ❌ | — | missing | 无 HTTP client | P1 |
| 发送成功确认机制 | ❌ | `delivery_result.py` (simulate only) | dry_run_only | 无真实确认 | P1 |
| 郭东北发送 | ❌ | — | missing | SOP 要求测试阶段发送给郭东北 | P1 |
| ReportIntent 解析 | ✅ | `sop/report_intent.py` | implemented | — | — |
| DeliveryResult 模拟 | ✅ | `sop/delivery_result.py` | dry_run_only | — | — |
| LifecycleCloseout | ✅ | `sop/delivery_result.py` | implemented | 只在本地测试链中使用 | — |

**结论：report-sender 全部缺失。** 有 `report_intent` 和 `delivery_result` 的本地模拟框架，但无任何真实发送能力。

> **⚠️ R38 correction (2026-05-28):** 上述"全部缺失"指 sop-data-hub 内部能力，但外部发送能力实际存在且经过生产验证：
> - **wx-ui-bridge** (`/Users/qicai21/projects/ai-tools/mcp/wx-ui-bridge`) 提供 `build_send_text_workflow` / `build_send_file_workflow` / `build_send_image_workflow`，可驱动微信桌面客户端发送文本、文件、图片
> - **Hermes skill** `wx-ui-bridge-group-image-send` 已验证发送至 GROUP001/GROUP003/GROUP102 及联系人郭东北
> - **wx-ops-agent** 的 `send_image_via_bridge()` 封装了图片发送（仅图片，不含文件/文本）
> - **Hermes `send_message`** 工具可直接用于 Telegram 发送
>
> **修正评级：** report-sender → **🟡 外部能力存在，sop-data-hub 未集成**（优先级从 P0 降至 P1）。详见 `reports/wx_ops_report_sender_audit_r38.md`。

---

### 7. contract intelligence（合同智能）

| 能力 | 已实现 | 文件 | 状态 | 缺口 | 优先级 |
|------|:--:|------|:--:|------|:--:|
| 合同目录 | ❌ | — | missing | 无合同文件目录 | P2 |
| 合同文件索引 | ❌ | — | missing | 无索引 | P2 |
| 合同要素抽取 | ❌ | — | missing | 无 NLP/extraction | P2 |
| contracts 表 CRUD | ✅ | `data_agent/agent.py` | implemented | — | — |
| 甲方/乙方 | ✅ | `contracts` table: party_a, party_b | implemented | — | — |
| 标的物 | ✅ | `contracts` table: cargo_name | implemented | — | — |
| 运输方式 | ✅ | `contracts` table: transport_mode | implemented | — | — |
| 集装箱/整车 | 部分 | `contracts` table: transport_type | implemented | 无明确集装箱/整车区分 | P2 |
| 发站/到站 | ✅ | `contracts` table: origin_station, destination_station | implemented | — | — |
| 价格 | ✅ | `contracts` table: price | implemented | — | — |
| 结算方式 | ❌ | — | missing | 无此字段 | P2 |
| 特殊条款 | ❌ | — | missing | 无此字段 | P2 |
| 合同要素 → SOP | ❌ | — | missing | `contracts` 表未与 `release_batches.contract_no` 联动 | P1 |
| 合同要素 → 项目配置 | ❌ | — | missing | SOP YAML 无 contract 引用 | P2 |
| 合同要素 → 数据库 | 部分 | `release_batches.contract_no` FK-like | implemented | 无实际 FK 约束 | P2 |

**结论：contract intelligence 仅有基础 CRUD。** 有 contracts 表存储结构化合同数据，但无文档解析、无要素抽取、无 SOP 联动。

---

## Part B: 系统能力结论

### Q1: 当前 sop-data-hub 是否已经具备"从 SOP YAML 生成监听计划"的能力？

**是。** `SOPWatcher` → `load_project_sop` → `SopMonitoringPlanCompiler` → `wechat_monitoring_plan` 链路完整。4 个项目全部可编译 monitoring plan。但仅 `jilin_jingang` 的 YAML 有完整的 `flows` 段可供编译 task plan。

### Q2: 当前是否已经具备"从 SOP YAML 生成执行任务链"的能力？

**部分具备。** `SOPTaskCompiler` 可将 SOP YAML 的 `flows` 段编译为 `ExecutableTaskPlan`。吉林金钢 28 tasks 完整编译。但 chaoyang/jiusan/zhongtang 的 YAML 没有 `flows` 段，无法编译 task plan。

### Q3: 当前是否已经具备"按任务链真实执行"的能力？

**不具备。** 28 个 task 中 16 个 missing、4 个 prototype、3 个 dry_run_only。跨模块来看：数据处理 2 个 implemented、95306 0 个 implemented、报告发送 0 个 implemented、DB 状态推进 0 个 implemented。整个执行链只有一个真实的 data-processing executor（departure_text_parser）。

### Q4: 哪些 executor 已实现？

1. `parse_departure_text` — `departure_text_parser.py` (R33)
2. `classify_message` — `monitoring_plan_matcher.py`
3. `extract_release_notice_json` — `runner.py` (OCR pipeline)
4. `identify_project` — `monitoring_plan_matcher.py` (fallback match)
5. `create_release_batch` — `agent.py`
6. `update_release_batch_fields` — `agent.py` (upsert)
7. `query_release_batches` — `agent.py`

### Q5: 哪些 executor 是 prototype？

1. `generate_departure_excel_task` — `gen_jljg_excel.py` (硬编码)
2. `generate_factory_transport_json_task` — `gen_jljg_excel.py` (print-only)

### Q6: 哪些 executor missing？

1. `enrich_release_batch` — freight_detail_flow
2. `build_time_window` — departure_flow
3. `query_95306_waybills` — departure_flow
4. `create_pending_task` — departure_flow
5. `extract_wagon_no/container_no/waybill_no` — departure_flow
6. `check_existing_wagon_shipments` — departure_flow
7. `bind_wagons_to_release_batch` — departure_flow
8. `create_wagon_shipments` — departure_flow
9. `dry_run_receiver_system` — departure_flow
10. `poll_shipment_snapshots` — tracking_flow
11. `update_dashboard_state` — tracking_flow
12. `update_wagon_arrival_status` — tracking_flow
13. `update_dispatch_status` (auto) — tracking_flow
14. `mark_confirmed_received` — tracking_flow
15. `close_dashboard_state` — tracking_flow

### Q7: 哪些缺口是系统性缺口，而不是吉林金钢单项目缺口？

| 缺口 | 类型 | 影响范围 |
|------|------|---------|
| DB schema 缺 6 列 | 系统性 | 所有普通货运项目 |
| 无定时轮询机制 | 系统性 | 所有 95306 tracking |
| 无真实报告发送 | 系统性 | 所有项目的 report delivery |
| 无 contract → SOP 联动 | 系统性 | 所有有合同的货运项目 |
| chaoyang/jiusan/zhongtang YAML 无 flows 段 | 项目级 | 仅影响个别项目 |
| GROUP005/013 fallback 缺失 | 系统性 | freight_detail_flow 路由 |
| 消息去重缺失 | 系统性 | 所有群的消息去重 |

---

## Part C: 下一阶段分层路线图

### P0：系统闭环最低要求

| # | 能力 | 描述 | 依赖 |
|---|------|------|------|
| P0-1 | DB schema migration | 添加 `order_identifier`, `cargo_name_detail`, `container_no`, `waybill_no`, `delivered_at`, `confirmed_received_at` | — |
| P0-2 | enrich_release_batch executor | 从 freight_detail_text 提取 order_identifier/contract_no/cargo_name_detail，写入 release_batches | P0-1 |
| P0-3 | build_time_window executor | 从 DepartureCandidate.message_time 构造 ±60m 窗口 | R33 |
| P0-4 | query_95306_waybills executor | 用时间窗口查询 95306 shipments | P0-3 |
| P0-5 | create_wagon_shipments executor | 从 95306 结果创建 wagon_shipments 行 | P0-1 |
| P0-6 | 定时 95306 轮询 | 将 shipment_status_sync 注册为 cron job | — |
| P0-7 | confirmed_received 逻辑 | 全部 wagon 交付后推进 dispatch_status → confirmed_received | P0-1 |

### P1：业务自动化增强

| # | 能力 | 描述 |
|---|------|------|
| P1-1 | Excel generation (data-driven) | 拆掉 gen_jljg_excel.py 硬编码 |
| P1-2 | factory JSON generation | 真实 JSON，非 print-only |
| P1-3 | report delivery (WeChat/Telegram) | 真实发送能力 |
| P1-4 | HTTP delivery (factory upload) | 真实 HTTP client |
| P1-5 | 消息去重 | group_id + seq 幂等 |
| P1-6 | contract → SOP 联动 | contract_no 关联 release_batches |
| P1-7 | GROUP005/013 fallback routing | monitoring_plan_matcher 扩展 |

### P2：报告体验和运维增强

| # | 能力 | 描述 |
|---|------|------|
| P2-1 | PDF generation | 发运报告 PDF |
| P2-2 | contract intelligence (NLP) | 合同要素抽取 |
| P2-3 | SOP YAML 标准化 (三项目) | chaoyang/jiusan/zhongtang 添加 flows 段 |
| P2-4 | dashboard closeout 自动化 | close_dashboard_state |
| P2-5 | SOP acceptance 规则校验 | 10 条 acceptance 规则自动验证 |

---

**审计完成。零代码变更。零数据库修改。**
