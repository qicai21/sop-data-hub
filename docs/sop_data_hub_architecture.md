# SOP Data Hub Architecture

**Version:** v0.2 (post R30-R37)
**Last updated:** 2026-05-28

---

## 一、六大工作面 + 合同层

```
                    ┌──────────────────┐
                    │  1. sop-compiler  │  ← SOP YAML → monitoring_plan → task_plan
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌──────────────────┐
│ 2. wx-tracker │  │ 3. 95306-tracker│  │ 4. data-processing│
│  监听 + 匹配   │  │  状态跟踪 + 同步  │  │  executors        │
└───────┬───────┘  └────────┬────────┘  └────────┬─────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │   5. db executors │  ← 读写 sop_agent.db
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ 6. report-sender  │  ← 发送 Excel / JSON / Telegram
                    └──────────────────┘

                    ┌──────────────────┐
                    │ 7. contract       │  ← 合同要素抽取 + 数据库 + SOP 联动
                    │    intelligence    │
                    └──────────────────┘
```

---

## 二、数据流

```
微信消息 (wx-ops-agent/data/chat_records/**/*.jsonl)
    │
    ▼
[SourceWatcher] ──→ MessageEvent ──→ monitoring_plan_matcher
    │                                      │
    │                                      ▼
    │                              MessageMatchResult
    │                                      │
    │                          ┌───────────┴───────────┐
    │                          │                       │
    │                          ▼                       ▼
    │                   image messages           text messages
    │                          │                       │
    │                          ▼                       ▼
    │                   OCR pipeline          departure_text_parser
    │                   (runner.py)           freight_detail_text matcher
    │                                                    │
    │                          ┌─────────────────────────┘
    │                          │
    ▼                          ▼
[TaskExecutionRegistry] ──→ TaskExecutionTrace
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │   Task Plan Execution     │
                    │                           │
                    │  implemented → execute     │
                    │  missing     → blocked     │
                    │  prototype   → data-driven │
                    └──────────┬───────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
            sop_agent.db          95306_collection.sqlite3
            ├ release_batches      ├ shipments
            ├ wagon_shipments      └ (read-only by sync tool)
            ├ contracts
            ├ departure_records
            ├ inspection_ingestion_candidates
            ├ report_tasks
            └ image_ingestion_audit
```

---

## 三、executor 状态定义

| 状态 | 含义 | 示例 |
|------|------|------|
| `implemented` | 完整的、可执行的实现 | `parse_departure_text` → `departure_text_parser.py` |
| `missing` | 无实现 | `build_time_window`, `query_95306_waybills` |
| `prototype` | 有硬编码原型，不可直接用于生产 | `generate_departure_excel_task` → `gen_jljg_excel.py` |
| `dry_run_only` | 仅有本地模拟，无真实发送 | `send_excel_task` → `delivery_result.py` |
| `partial` | 部分实现，关键步骤缺失 | (未出现当前系统中) |

---

## 四、SOP → task → executor → state 链路

```
SOP YAML (config/project_sops/jilin_jingang.yaml)
    │
    ├─ load_project_sop()
    │  └─ ProjectSOP (listening_tasks + routing)
    │
    ├─ SopMonitoringPlanCompiler
    │  └─ wechat_monitoring_plan
    │     └─ GROUP001/GROUP005/GROUP013 → watch_items
    │
    ├─ SOPTaskCompiler
    │  └─ ExecutableTaskPlan (flows → tasks)
    │     ├─ release_notice_flow:  5 tasks (detect → create → update)
    │     ├─ freight_detail_flow:  1 task  (enrich_release_batch)
    │     ├─ departure_flow:      14 tasks (detect → 95306 → extract → write → excel → ship)
    │     └─ tracking_flow:        4 tasks (poll → arrived → delivered → confirmed)
    │
    ├─ TaskExecutionRegistry
    │  └─ MessageEvent → TaskExecutionTrace
    │     ├─ matched_flow / matched_node
    │     ├─ actions with executor_status
    │     └─ next_missing_task
    │
    └─ ShipmentStatusSync (95306 → sop_agent.db)
       ├─ car_no + destination match
       ├─ 95306 snapshot → wagon_shipments (departed_at, arrived_at)
       └─ batch dispatch_status → delivered
```

---

## 五、合同要素如何进入系统

### 当前状态

```
contracts table (sop_agent.db)
├─ party_a / party_b             ← 甲方/乙方
├─ cargo_name                    ← 标的物
├─ transport_mode / transport_type ← 运输方式
├─ origin_station / destination_station ← 发站/到站
├─ price                         ← 价格
├─ doc_path                      ← 合同文件路径
└─ created_at / updated_at
```

### 与 SOP 的关联

```
release_batches.contract_no  →  contracts  (通过 contract_no 字符串关联，无 FK)
release_batches.contract_id  →  contracts.id  (FK 字段存在但多数行为 NULL)
```

### 缺口

1. 无合同文件目录 / 索引
2. 无 OCR/PDF 解析 → 合同要素需手动录入
3. 合同要素不进入 SOP YAML（SOP YAML 无 contract 引用段）
4. 合同要素不进入 project config（`project_meta` 无合同相关字段）
5. 结算方式 / 特殊条款无 DB 字段

### 建议路径

```
合同 PDF
  → OCR/要素抽取 (pipeline)
  → contracts 表
  → release_batches.contract_no 关联
  → SOP YAML project_meta.contract_ref (新字段)
  → 报表/Excel 中自动填入合同信息
```

---

## 六、关键服务与入口

### live_service

```
scripts/run_live_service.py
├─ --status          → JSON status (sop_runtime + sop_task_runtime + sop_task_trace_runtime)
├─ --once            → 单次 poll
├─ --fixture-dir     → SOP YAML 目录 (默认 config/project_sops/)
├─ --runtime-root    → 运行时输出 (runtime/events, runtime/dashboard_*, runtime/task_traces)
└─ --chat-records-root → wx-ops-agent 消息源
```

### CLI 工具

| 命令 | 功能 |
|------|------|
| `business_query.py reconcile-inspection` | 95306 reconciler (plan/commit) |
| `business_query.py render-dispatch-board` | HTML dashboard |
| `business_query.py set-dispatch-status` | 手动设 dispatch_status |
| `sync_shipment_status_from_95306.py` | 95306 → wagon_shipments 同步 |
| `gen_jljg_excel.py` | 吉林金钢 Excel (硬编码原型) |

### cronjob 建议

```json
{
  "schedule": "30m",
  "script": "scripts/sync_shipment_status_from_95306.py --ship-name <ship> --apply",
  "no_agent": true
}
```

---

## 七、R30-R37 演进记录

| Round | 内容 | 新增能力 |
|-------|------|---------|
| R30 | SOP v0.2 gap audit | capability baseline |
| R31 | freight_detail_text pattern verify | text_patterns YAML 验证 |
| R32 | text_patterns propagation | RoutingRule → compiler → matcher |
| R33 | departure text parser | `departure_text_parser.py` |
| R34 | SOP task compiler | `ExecutableTaskPlan`, 28 tasks for jilin_jingang |
| R35 | task execution registry | `TaskExecutionTrace`, message-to-task routing |
| R36 | 95306 status sync | `ShipmentStatusSync`, 蓝鳍/木森17/长航滨海 synced |
| R37 | capability map audit | 本文档 |
