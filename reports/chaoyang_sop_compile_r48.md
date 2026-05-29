# Report: chaoyang SOP Compile & Task Plan Audit (R48)

| 字段 | 内容 |
|------|------|
| Order ID | R48 |
| 执行日期 | 2026-05-29 |
| 执行者 | local Hermes |
| commit (source) | 9ea2b4e1aea7b4f8fcd3329a43fb0db9a0a29ba5 |
| branch | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

chaoyang.yaml **可以正确加载**，`live_service --status` 显示 `chaoyang_steel` 已 loaded。但当前 `SOPTaskCompiler` 硬编码了 4 个 flow name，**只编译了 2/5 flows**（release_notice_flow、tracking_flow），缺失 `inspection_notice_flow`、`business_text_flow`、`report_delivery_flow`。

即使补上 compiler flow 列表，除去已实现的 shared executor，chaoyang 仍有 **17 个 actions 为 missing**，其中最关键的是 `inspection_notice_flow` 的 6 个 OCR 提取 action 和匹配/查询 action。

## 2. YAML 加载验证

| 检查项 | 结果 |
|--------|------|
| `yaml.safe_load` | ✅ 成功 |
| `load_project_sop` | ✅ project_id=chaoyang_steel, 2 listening_tasks |
| `live_service --status` | ✅ loaded_projects 含 chaoyang_steel |
| 文件 hash | `3d48c3bb65fa5f71` |

## 3. 编译结果

### 3.1 编译器覆盖

```
Hardcoded flows: release_notice_flow, freight_detail_flow, departure_flow, tracking_flow
Chaoyang flows:  release_notice_flow, inspection_notice_flow, business_text_flow, tracking_flow, report_delivery_flow

Compiler picks up:  release_notice_flow, tracking_flow        ← 2/5
Compiler misses:    inspection_notice_flow, business_text_flow, report_delivery_flow  ← 3/5
```

**根因**：`SOPTaskCompiler.compile()` 第 330 行硬编码了 flow name 列表，不兼容 chaoyang 的 5 个 flow。

### 3.2 release_notice_flow (4 nodes)

| node | primary action | status |
|------|---------------|--------|
| detect_release_notice | classify_message | ✅ implemented |
|  | extract_release_notice_json | ✅ implemented |
| identify_project | match_project_by_destination_and_cargo | ❌ missing |
| find_existing_release_batch | query_release_batches | ✅ implemented |
| create_or_update_release_batch | create_release_batch | ✅ implemented |
|  | update_release_batch_fields | ✅ implemented |

### 3.3 tracking_flow (4 nodes)

| node | primary action | status |
|------|---------------|--------|
| track_95306_status | poll_shipment_snapshots | ✅ implemented |
| arrived_destination | update_wagon_arrival_status | ❌ missing |
|  | update_dashboard_state | ❌ missing |
| delivered | update_dispatch_status | ❌ missing |
|  | mark_confirmed_received | ❌ missing |
| close_batch_if_all_confirmed | close_dashboard_state | ❌ missing |
|  | mark_release_batch_completed | ❌ missing |

### 3.4 未编译的 3 个 flow（gap）

**inspection_notice_flow (6 nodes, 13 actions)** — chaoyang 的**核心流程**：

| node | actions | 现状 |
|------|---------|------|
| detect_inspection_notice | classify_message, extract_inspection_notice_json | classify_message ✅ / extract_inspection_notice_json ❌ |
| extract_inspection_notice_fields | extract_ship_name, extract_destination_station, extract_cargo_name, extract_car_count, extract_wagon_numbers, extract_notice_time | 全部 ❌ |
| match_release_batch | match_release_batch_by_ship_destination_cargo | ❌ |
| query_95306_by_inspection_notice | query_95306_shipments_by_window_or_wagon_numbers | ❌ (可复用 query_95306_waybills) |
| create_wagon_shipments | create_wagon_shipments, bind_wagons_to_release_batch, check_existing_wagon_shipments | ✅ 全部 implemented |
| enter_tracking_phase | → track_95306_status | 转 tracking_flow |

**business_text_flow (1 node)**：

| node | action | status |
|------|--------|--------|
| capture_business_context | store_message_context | ❌ missing |

**report_delivery_flow (4 nodes)**：

| node | action | status |
|------|--------|--------|
| generate_departure_excel | generate_departure_excel_task | 🟡 prototype |
| generate_factory_json | generate_factory_transport_json_task | 🟡 prototype |
| send_test_report | send_excel_task | 🟡 dry_run_only |
| send_production_report | send_excel_task | 🟡 dry_run_only |

### 3.5 task_resolver (7 tasks)

| resolver | status |
|----------|--------|
| release_notice_image (ocr_extraction) | ❌ missing |
| inspection_notice_image (ocr_extraction) | ❌ missing |
| create_wagon_shipments (db_write) | ✅ implemented |
| shipment_status_sync (95306_tracking) | ❌ missing |
| departure_excel (excel_generation) | 🟡 prototype |
| factory_json (json_generation) | 🟡 prototype |
| report_delivery (wx_delivery) | ❌ missing |

### 3.6 统计汇总

| 维度 | 数量 |
|------|------|
| 总 unique actions | 29 |
| implemented | 12 |
| missing | 17 |
| prototype | 2 |
| dry_run_only | 2 (send_excel_task / telegram_json_delivery_task，跨项目共享) |

当前 compiler 可见统计（仅 2 flow）：

| 维度 | 数量 |
|------|------|
| total_tasks | 15 (= 4 release + 4 tracking + 7 resolver) |
| implemented | 5 |
| missing | 8 |
| prototype | 2 |
| dry_run_only | 0 |

## 4. 业务规则核对

| 规则 | YAML 体现 | 状态 |
|------|-----------|------|
| 出港计划通知单图片为核心入口 | routing: category_in:[出港计划通知单] | ✅ |
| 检装车通知单图片为核心入口 | routing: category_in:[检装车通知单] | ✅ |
| 业务文字只作上下文 | text_is_context_only: true | ✅ |
| 不同出港计划通知单对应不同 release_batch | separate_notice_means_separate_release_batch: true | ✅ |
| 同一船名可存在多个 release_batch | same_ship_can_have_multiple_release_batches: true | ✅ |
| 检装车通知单优先使用车号查询 95306 | prefer_wagon_numbers: true | ✅ |
| 95306 库只读 | rail_db_read_only: true | ✅ |
| 测试报送 GROUP013 或郭东北 | send_test_report → GROUP013 / 郭东北 | ✅ |
| 生产报送 GROUP004 | send_production_report → GROUP004 | ✅ |
| 不同放货批次不得合并 | do_not_merge_release_batches: true (三处) | ✅ |

## 5. 当前最关键的 missing executor

按优先级排序：

| 优先级 | action | 所属 flow | 原因 |
|--------|--------|-----------|------|
| **P0** | extract_inspection_notice_json | inspection_notice_flow | 朝阳的检装车 OCR 提取器不存在 |
| **P0** | extract_ship_name / extract_destination_station / extract_cargo_name / extract_car_count / extract_wagon_numbers / extract_notice_time | inspection_notice_flow | 检装车字段提取全部缺失 |
| **P0** | match_release_batch_by_ship_destination_cargo | inspection_notice_flow | 无法将检装车匹配到 release_batch |
| **P0** | query_95306_shipments_by_window_or_wagon_numbers | inspection_notice_flow | 可复用 query_95306_waybills，但需新 wrapper |
| P1 | match_project_by_destination_and_cargo | release_notice_flow | 项目识别 - 朝阳西关键词匹配 |
| P1 | update_wagon_arrival_status / update_dispatch_status / mark_confirmed_received | tracking_flow | 可复用 shipment_status_sync，需新 status 映射 |
| P1 | close_dashboard_state / mark_release_batch_completed | tracking_flow | 看板状态更新 |

## 6. 场景模拟：发送木森17检装车通知单能走到哪一步

假设郭东北在 GROUP013（数据单发群）发送木森17检装车通知单图片：

```
当前系统能力：
  1. wx-ops-agent 拉取图片 ✅
  2. classify_message → 可能识别为"检装车通知单" ✅
  3. extract_inspection_notice_json → ❌ 不存在！停在这里。

后续链条（全部缺失）：
  4. extract_ship_name etc → ❌
  5. match_release_batch_by_ship_destination_cargo → ❌
  6. query_95306_shipments_by_window_or_wagon_numbers → ❌
  7. create_wagon_shipments → ✅ 可用（但前面数据没准备好）
  8. tracking/sync → ✅ 部分可用

结论：木森17检装车通知单的自动化处理在步骤 3 就停住了。
当前最多能走到 classify_message 识别出类别，但无法继续。
```

对比吉林金钢（jilin_jingang）的 departure_flow，它依赖的是 **文字解析**（departure_text_parser），不依赖 OCR 图片字段提取。朝阳的 inspection_notice_flow 需要的是 **OCR 图片字段提取**，这是一个全新的能力缺口。

## 7. 下一步建议

### 7.1 立即（P0）

1. **修复 compiler 硬编码**：将 flow name 列表从硬编码改为动态遍历 `data['flows']`，使 `inspection_notice_flow`、`business_text_flow`、`report_delivery_flow` 可编译。
2. **实现 inspection_notice_json 提取器**：新增 `src/ops_hub/sop/inspection_notice_extractor.py`，从检装车通知单 OCR JSON 中提取 ship_name、destination_station、cargo_name、car_count、wagon_numbers、notice_time。
3. **实现 match_release_batch_by_ship_destination_cargo**：从已有 `query_release_batches` 复用并按船名/到站/货名匹配。
4. **实现 query_95306_shipments_by_window_or_wagon_numbers wrapper**：包装已有的 `query_95306_waybills`，增加车号查询优先逻辑。
5. **注册 executor status**：将新增 action 注册到 `_EXECUTOR_STATUS`。

### 7.2 后续（P1）

6. **tracking 下游 status 更新**：已有 `shipment_status_sync` 可覆盖大部分 tracking 节点，但需要增加 `update_wagon_arrival_status`、`close_dashboard_state` 等细粒度 action。
7. **report_delivery**：excel/prototype 已有，但需要特定朝阳钢铁模板和发送逻辑。
8. **business_text_flow**：`store_message_context` 为低优先级，仅存文本上下文。

### 7.3 注意

- `inspection_notice_flow` 与 `departure_flow` 不同：前者基于 OCR 图片字段提取（字段在图片上），后者基于文字解析（字段在文本消息中）。不能直接复用 departure_text_parser。
- 已有木森17的 47 车 wagon_shipments 在 sop_agent.db 中，可作为检装车 OCR 提取的验收测试数据。

## 8. 改动文件

- 无代码改动（纯验证任务）

## 9. Git

- branch: codex/sop-real-sop-topology-audit-20260525
- commit (baseline): 9ea2b4e
- PR: 无（验证任务，不涉及代码变更）
