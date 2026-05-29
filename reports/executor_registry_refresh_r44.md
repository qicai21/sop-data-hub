# Report: R44 — executor registry refresh after R40-R42

| 字段 | 内容 |
|------|------|
| Order | R44 |
| 执行日期 | 2026-05-29 |
| Branch | codex/sop-real-sop-topology-audit-20260525 |
| Tests | 192 passed, 0 failed |

## 1. 结论

R44 完成。`_EXECUTOR_STATUS` registry 已与 R40-R42 实现的 executor 同步。implemented: 5→9, missing: 16→12。

## 2. R43 中误报 missing 的 executor 清单

| Executor | R40-R42 实现 | 文件 | 修正前 | 修正后 |
|----------|:--:|------|:--:|:--:|
| `extract_freight_detail` | R40 | freight_detail_extractor.py | missing | **implemented** |
| `enrich_release_batch` | R41 | enrich_release_batch.py | missing | **implemented** |
| `build_time_window` | R42 | shipment_query_window.py | missing | **implemented** |
| `query_95306_waybills` | R42 | query_95306_shipments.py | missing | **implemented** |
| `poll_shipment_snapshots` | R36 | shipment_status_sync.py | missing | **implemented** |

## 3. 修正后的 executor 状态矩阵

### release_notice_flow

| 节点 | 状态 | 原因 |
|------|:--:|------|
| detect_release_notice | implemented | classify_message + OCR pipeline |
| identify_project | missing | YAML 无 action，编译器无法匹配 |
| find_existing_release_batch | implemented | query_release_batches |
| create_or_update_release_batch | implemented | create_release_batch |
| update_existing_release_batch | implemented | update_release_batch_fields |

### freight_detail_flow

| 节点 | 状态 | 原因 |
|------|:--:|------|
| enrich_release_batch | **implemented** | R41 + registry 修正 |

### departure_flow

| 节点 | 状态 | 原因 |
|------|:--:|------|
| detect_departure_message | implemented | parse_departure_text |
| build_95306_query_window | **implemented** | R42 QueryWindow |
| query_95306 | **implemented** | R42 query_95306_shipments_by_window |
| query_result_found | missing | create_pending_task 未实现 |
| extract_wagons_and_waybills | missing | extract_wagon_no 等未实现 |
| deduplicate_departure_records | missing | check_existing 未实现 |
| match_release_batch | missing | bind_wagons_to_release_batch 未实现 |
| write_departure_records | **missing** | **create_wagon_shipments P0** |
| generate_departure_excel | prototype | 硬编码 Excel |
| generate_factory_json | prototype | print-only JSON |
| send_test_excel | dry_run_only | simulate only |
| telegram_json_delivery | dry_run_only | simulate only |

### tracking_flow

| 节点 | 状态 | 原因 |
|------|:--:|------|
| track_95306_status | **implemented** | R36 shipment_status_sync |
| arrived_destination | missing | update_dashboard_state 未实现 |
| delivered | missing | update_dispatch_status 未实现 |
| confirmed_received | missing | mark_confirmed_received 未实现 |

### task_resolver

| 任务 | 状态 |
|------|:--:|
| departure_excel | prototype |
| factory_json | prototype |
| telegram_delivery | dry_run_only |
| receiver_upload | missing |

## 4. 数量变化

| 指标 | 修正前 | 修正后 | 变化 |
|------|:--:|:--:|:--:|
| **implemented** | 5 | **9** | +4 ✅ |
| **missing** | 16 | **12** | -4 ✅ |
| prototype | 4 | 4 | — |
| dry_run_only | 3 | 3 | — |

## 5. live_service --status 摘要

```
sop_task_runtime:
  implemented_task_count: 9
  missing_task_count: 12
  jilin_jingang: implemented=9, missing=12, prototype=4, dry_run_only=3
```

## 6. 测试结果

```
tests/functional: 192 passed, 0 failed
R44 new tests: 16 passed
```

新测试覆盖：

| # | 测试 | 状态 |
|---|------|:--:|
| 1 | query_95306_waybills 注册为 implemented | PASS |
| 2 | enrich_release_batch 注册为 implemented | PASS |
| 3 | extract_freight_detail 注册为 implemented | PASS |
| 4 | poll_shipment_snapshots 注册为 implemented | PASS |
| 5 | build_time_window 注册为 implemented | PASS |
| 6 | create_wagon_shipments 仍为 missing | PASS |
| 7 | bind_wagons_to_release_batch 仍为 missing | PASS |
| 8 | report_sender_adapter 仍为 dry_run_only | PASS |
| 9 | prototype 不误标 implemented | PASS |
| 10 | jilin_jingang plan implemented >= 9 | PASS |
| 11 | freight_detail_flow 全 implemented | PASS |
| 12 | departure_flow query nodes implemented | PASS |
| 13 | write_departure_records 仍 missing | PASS |
| 14 | 蓝鳍 trace next_missing 不是旧 implemented | PASS |
| 15 | 蓝鳍 trace implemented actions 正确 | PASS |
| 16 | live_service --status 反映更新 | PASS |

## 7. 修改文件

| 文件 | 变更 |
|------|------|
| src/ops_hub/sop/sop_task_compiler.py | +5 entries to `_EXECUTOR_STATUS` |
| tests/functional/test_sop_task_compiler.py | 2 tests: missing→implemented |
| tests/functional/test_task_execution_registry.py | 5 tests: updated assertions |

## 8. 下一轮: R45 create_wagon_shipments

R44 完成后，departure_flow 的阻塞点已从 build_time_window/query_95306 推进到 create_wagon_shipments。

当前可自动走到：
```
departure_text → parse → QueryWindow → 95306 query → candidates
```

卡在:
```
candidates → create_wagon_shipments → write to DB
```

R45 实现 create_wagon_shipments 后，蓝鳍 departure text 可完成全链路（写 DB）。
