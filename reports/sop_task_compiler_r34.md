# R34: Compile SOP YAML flows into executable task plan

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Task:** Implement SOPTaskCompiler — YAML flows → ExecutableTaskPlan

---

## 一、实现

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/ops_hub/sop/sop_task_compiler.py` | `ExecutableTask`, `ExecutableTaskPlan`, `SOPTaskCompiler`, `compile_project_sop()` |
| `tests/functional/test_sop_task_compiler.py` | 23 tests |

### 修改文件

| 文件 | 说明 |
|------|------|
| `scripts/run_live_service.py` | `--status` 增加 `sop_task_runtime` 段 |

### 编译器架构

```
SOP YAML (flows + runtime.task_resolver)
    ↓
SOPTaskCompiler.compile()
    ↓
ExecutableTaskPlan
    ├── flows
    │   ├── release_notice_flow: [ExecutableTask × 5]
    │   ├── freight_detail_flow: [ExecutableTask × 1]
    │   ├── departure_flow:     [ExecutableTask × 14]
    │   └── tracking_flow:      [ExecutableTask × 4]
    └── task_resolver_tasks:     [ExecutableTask × 4]
```

### ExecutableTask 字段

| 字段 | 说明 |
|------|------|
| task_id | `{project_id}:{flow_name}:{node}` |
| project_id | SOP project_id |
| flow_name | release_notice / freight_detail / departure / tracking |
| node | SOP node name |
| actions | action 列表 |
| inputs | extract_fields |
| outputs | outputs |
| conditions | conditions dict |
| next_nodes | next / auto-linked |
| task_type | 来自 runtime.task_resolver |
| executor_status | implemented / missing / prototype / dry_run_only |
| evidence_file | 实现文件路径 |

### 实现状态注册表

**已实现:**
- parse_departure_text → `departure_text_parser.py`
- classify_message → `monitoring_plan_matcher.py`
- extract_release_notice_json → `runner.py`
- identify_project → `monitoring_plan_matcher.py`
- create_release_batch / update_release_batch_fields / query_release_batches → `agent.py`

**原型 (prototype):**
- generate_departure_excel_task → `gen_jljg_excel.py` (硬编码)
- generate_factory_transport_json_task → `gen_jljg_excel.py` (print-only)

**dry_run_only:**
- send_excel_task → `delivery_result.py` (simulate_delivery_result)
- telegram_json_delivery_task → `delivery_result.py` (simulate_delivery_result)

**未实现 (16 个):**
- build_time_window, query_95306_waybills, create_pending_task, extract_wagon_no,
  extract_container_no, extract_waybill_no, check_existing_wagon_shipments,
  bind_wagons_to_release_batch, create_wagon_shipments, dry_run_receiver_system,
  poll_shipment_snapshots, update_dashboard_state, update_wagon_arrival_status,
  update_dispatch_status, mark_confirmed_received, close_dashboard_state

---

## 二、测试

```
pytest tests/functional/test_sop_task_compiler.py -v
23 passed

pytest tests/functional -v
97 passed (48 legacy + 7 R32 + 19 R33 + 23 R34)
```

### 测试覆盖

| # | Test | 结果 |
|---|------|:--:|
| 1 | compile returns ExecutableTaskPlan | ✅ |
| 2-3 | project_id / project_name | ✅ |
| 4-7 | 四个 flow 全部编译 | ✅ |
| 8-10 | departure_flow 包含 parse_departure_text / build_time_window / query_95306 | ✅ |
| 11-12 | tracking_flow 包含 poll / mark_confirmed_received | ✅ |
| 13 | parse_departure_text = implemented | ✅ |
| 14 | query_95306_waybills = missing | ✅ |
| 15 | poll_shipment_snapshots = missing | ✅ |
| 16 | create_wagon_shipments = missing | ✅ |
| 17-19 | task_resolver excel/telegram/http | ✅ |
| 20 | summary 指标 ≥ 阈值 | ✅ |
| 21 | 无副作用 | ✅ |
| 22 | to_dict 包含所有 flow | ✅ |
| 23 | 所有 task 字段完整 | ✅ |

---

## 三、live_service --status 新增

```json
{
  "sop_task_runtime": {
    "loaded_task_plans": 4,
    "project_ids": ["chaoyang_steel", "jilin_jingang_jinzhou", "jiusan", "zhongtang_special_steel"],
    "missing_task_count": 16,
    "implemented_task_count": 5,
    "plans": [
      {
        "project_id": "jilin_jingang_jinzhou",
        "total_tasks": 28,
        "implemented": 5,
        "missing": 16,
        "prototype": 4,
        "dry_run_only": 3
      }
    ]
  }
}
```

chaoyang_steel / jiusan / zhongtang 的 task 数为 0 — 它们的 YAML 格式不含 `flows` 段。

---

## 四、吉林金钢任务统计

| 状态 | 数量 | 说明 |
|------|:---:|------|
| total_tasks | 28 | 4 个 flow + 4 个 task_resolver |
| implemented | 5 | 消息匹配、项目识别、放货批次创建/更新、发运文本解析 |
| missing | 16 | 95306 查询、wagon_shipments 写入、tracking、delivery |
| prototype | 4 | Excel/JSON 生成（硬编码脚本） |
| dry_run_only | 3 | Excel/JSON/Telegram 发送（仅本地模拟） |

---

## 五、下一轮建议

**优先补 executor:**

| # | Executor | 依赖 | 优先级 |
|---|----------|------|:------:|
| 1 | build_time_window | message_time (R33 已有) | P0 |
| 2 | query_95306_waybills | inspection_95306_reconciler 可复用 | P0 |
| 3 | create_wagon_shipments | DB schema 扩列 (P0-7) | P0 |
| 4 | poll_shipment_snapshots | 95306 DB 连接 | P0 |
| 5 | generate_departure_excel | gen_jljg_excel.py 数据驱动化 | P1 |

**与 R30 对齐：** R34 的 task 统计直接量化了 R30 的缺口 — 28 个 task 中 16 missing = 57% 未实现。

---

## 六、未做

- 不执行任务
- 不写 sop_agent.db
- 不查 95306_collection.sqlite3
- 不生成 Excel / JSON
- 不改 jilin_jingang.yaml
- 不改 wx-ops-agent
- 不实现具体业务 executor

---

## 七、文件

- `src/ops_hub/sop/sop_task_compiler.py` — 新
- `tests/functional/test_sop_task_compiler.py` — 新（23 tests）
- `scripts/run_live_service.py` — 修改（+sop_task_runtime）
