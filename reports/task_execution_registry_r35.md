# R35: SOP Task Execution Registry and Message-to-Task Trace

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Task:** Minimal SOP Task Execution Registry + message-to-task trace

---

## 一、实现

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/ops_hub/sop/task_execution_registry.py` | `TaskExecutorAction`, `TaskExecutionTrace`, `TaskExecutionRegistry` |
| `tests/functional/test_task_execution_registry.py` | 16 tests |

### 修改文件

| 文件 | 说明 |
|------|------|
| `scripts/run_live_service.py` | `--status` 增加 `sop_task_trace_runtime` 段 |

### 数据结构

```
MessageEvent
    ↓
TaskExecutionRegistry.generate_trace()
    ↓
TaskExecutionTrace
    ├── trace_id
    ├── matched_flow / matched_node
    ├── actions: [TaskExecutorAction × N]
    │     ├── action name
    │     ├── executor_status: implemented | missing | prototype | dry_run_only
    │     └── evidence_file
    ├── generated_tasks / executable_tasks / missing_tasks
    ├── next_missing_task
    └── status: ready | blocked_missing_executor | no_matching_flow | dry_run_only
```

### 消息→flow 路由

| 优先级 | 规则 | 匹配 flow | 匹配 node |
|:---:|------|-----------|-----------|
| 1 | `departure_text_parser` return != "no_match" | departure_flow | detect_departure_message |
| 2 | 文本含 freight_detail text_patterns（合同号/标识号/货名等） | freight_detail_flow | enrich_release_batch |
| - | 其他 | — | no_matching_flow |

---

## 二、数据单发群补发数据验证

### 蓝鳍 departure 文本

输入：
```
"十四道，四平铁，蓝鳍，18车"
```

Trace：
```json
{
  "trace_id": "trace_wx_dep_001_...",
  "matched_flow": "departure_flow",
  "matched_node": "detect_departure_message",
  "project_id": "jilin_jingang_jinzhou",
  "status": "blocked_missing_executor",
  "generated_tasks": 14,
  "executable_tasks": 1,
  "missing_tasks": 10,
  "next_missing_task": "build_95306_query_window",
  "actions": [
    {"action": "parse_departure_text", "executor_status": "implemented"},
    {"action": "build_time_window", "executor_status": "missing"},
    {"action": "query_95306_waybills", "executor_status": "missing"},
    {"action": "create_pending_task", "executor_status": "missing"},
    {"action": "extract_wagon_no", "executor_status": "missing"},
    {"action": "extract_container_no", "executor_status": "missing"},
    {"action": "extract_waybill_no", "executor_status": "missing"},
    {"action": "check_existing_wagon_shipments", "executor_status": "missing"},
    {"action": "bind_wagons_to_release_batch", "executor_status": "missing"},
    {"action": "create_wagon_shipments", "executor_status": "missing"},
    {"action": "generate_departure_excel_task", "executor_status": "prototype"},
    {"action": "generate_factory_transport_json_task", "executor_status": "prototype"},
    {"action": "dry_run_receiver_system", "executor_status": "missing"},
    {"action": "send_excel_task", "executor_status": "dry_run_only"},
    {"action": "telegram_json_delivery_task", "executor_status": "dry_run_only"}
  ]
}
```

**蓝鳍 departure text 的解析链路：** parse_departure_text ✅ → **build_time_window 阻塞**

### 货运信息文本

输入：
```
"订单标识 CGR20260518174420，合同号 JGCG-SFY-HTNK20260501，货名 红土镍矿，批次 2"
```

Trace：
```json
{
  "matched_flow": "freight_detail_flow",
  "matched_node": "enrich_release_batch",
  "status": "blocked_missing_executor",
  "generated_tasks": 1,
  "executable_tasks": 0,
  "missing_tasks": 1,
  "next_missing_task": "enrich_release_batch",
  "actions": [
    {"action": "enrich_release_batch", "executor_status": "missing"}
  ]
}
```

**货运信息解析链路：** enrich_release_batch ❌ （无 executor，直接阻塞）

### 无关文本

```
"今天天气不错"
```

```
{"status": "no_matching_flow", "reason": "message did not match any SOP flow"}
```

---

## 三、测试

```
pytest tests/functional/test_task_execution_registry.py -v
16 passed

pytest tests/functional -v
113 passed (48 legacy + 7 R32 + 19 R33 + 23 R34 + 16 R35)
```

### 测试覆盖

| # | Test | 验证内容 | 结果 |
|---|------|---------|:--:|
| 1 | test_registry_loads_plan | registry 通过 plan 包含 departure_flow | ✅ |
| 2 | test_lanqi_departure_text_generates_trace | 蓝鳍发运文本 → departure_flow trace | ✅ |
| 3 | test_parse_departure_text_is_implemented_in_trace | trace 中 parse_departure_text=implemented | ✅ |
| 4 | test_build_time_window_is_missing_in_trace | trace 中 build_time_window=missing | ✅ |
| 5 | test_next_missing_task_is_build_95306_query_window | next_missing_task=build_95306_query_window | ✅ |
| 6 | test_freight_detail_text_generates_trace | 货运文本 → freight_detail_flow trace | ✅ |
| 7 | test_freight_detail_next_missing_is_enrich_release_batch | next_missing_task=enrich_release_batch | ✅ |
| 8 | test_irrelevant_text_no_matching_flow | 无关文本 → no_matching_flow | ✅ |
| 9 | test_no_external_side_effects | 不执行外部操作 | ✅ |
| 10 | test_trace_write_to_task_traces | write=True → trace JSON 写入 trace_dir | ✅ |
| 11 | test_freight_detail_with_car_keyword_still_matches_departure | "合同号"+"46车" → departure 优先 | ✅ |
| 12 | test_create_registry_for_project | create_registry_for_project() | ✅ |
| 13 | test_trace_has_all_required_fields | trace dict 包含所有必需字段 | ✅ |
| 14 | test_multiple_traces_have_different_ids | 不同消息 = 不同 trace_id | ✅ |
| 15 | test_trace_to_dict_serializable | json.dumps(trace.to_dict()) 成功 | ✅ |
| 16 | test_no_trace_dir_no_file_writes | trace_dir="" → 无文件写入 | ✅ |

---

## 四、live_service --status 新增

```json
{
  "sop_task_trace_runtime": {
    "enabled": true,
    "trace_dir": "/Users/qicai21/projects/repos/sop-data-hub/runtime/task_traces",
    "last_trace_count": 0,
    "last_trace_id": ""
  }
}
```

`runtime/task_traces/` 已 gitignored（`runtime/` 在 `.gitignore` 中）。

---

## 五、蓝鳍链路分析

### 用户于 5月23日18点向数据单发群补发蓝鳍相关数据

如果系统接入该消息，trace 会是：

**1. 发运文本**
```
消息: "十四道，四平铁，蓝鳍，18车"
matched_flow: departure_flow
parse_departure_text: ✅ implemented — 可以提取 destination=四平, car_count=18, ship=蓝鳍
build_time_window:     ❌ missing    — 无法构造 ±60m 窗口
query_95306_waybills:  ❌ missing    — 无法查询 95306
...以下 8 个 executor 全部 missing
status: blocked_missing_executor
```

**2. 货运信息**
```
消息: "订单标识 CGR...合同号 JGCG...货名 红土镍矿..."
matched_flow: freight_detail_flow
enrich_release_batch:  ❌ missing    — 无法写入 order_identifier / contract_no / cargo_name_detail
status: blocked_missing_executor
```

### 阻断 executor 排序

按流程优先级，最先需要补的 executor：

| # | Executor | 依赖 | 优先级 |
|---|----------|------|:------:|
| 1 | enrich_release_batch | DB order_identifier/cargo_name_detail 列 | P0 - 货运信息入链 |
| 2 | build_time_window | message_time (R33 已有) | P0 - 95306 查询入口 |
| 3 | query_95306_waybills | 95306 DB + DestinationCandidate | P0 - 车次查询 |
| 4 | create_wagon_shipments | wagon_shipments 表扩列 | P0 - 落库 |
| 5 | mark_confirmed_received | tracking DB 字段 | P0 - 状态推进 |

---

## 六、下一轮建议

1. **优先补 build_time_window executor** — 输入 `DepartureCandidate.message_time`，输出 `(start, end)` 窗口。最轻量的 executor，只做 datetime 运算。
2. **实现 query_95306_waybills** — 复用 `inspection_95306_reconciler._query_all_shipments_in_window()`，改用 message_time 驱动。
3. **建议用 R35 trace 做验收** — 每补一个 executor，trace 中 `missing_tasks` 减 1，`executable_tasks` 加 1。用 trace 观测实现进度。

---

## 七、未做

- 不执行真实业务（不查 95306、不写 DB、不生成 Excel、不发送报告）
- 不改 jilin_jingang.yaml
- 不改 wx-ops-agent
- 不改数据库 schema
- 不生成 Excel / JSON
- trace 文件不提交（runtime/ gitignored）

---

## 八、文件

- `src/ops_hub/sop/task_execution_registry.py` — 新
- `tests/functional/test_task_execution_registry.py` — 新（16 tests）
- `scripts/run_live_service.py` — 修改（+sop_task_trace_runtime）
