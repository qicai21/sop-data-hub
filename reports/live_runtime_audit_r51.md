# Report: Live Runtime Audit — jilin_jingang Execution Path (R51)

| 字段 | 内容 |
|------|------|
| Order ID | R51 |
| 执行日期 | 2026-05-29 |
| 执行者 | local Hermes |
| branch | codex/sop-real-sop-topology-audit-20260525 |
| 审计范围 | 吉林金钢 jilin_jingang_jinzhou |

## 1. 结论

**吉林金钢的 SOP executor 链完全没有被 live_service 调用。**

消息从微信群进入系统后，停在 **match → preview** 层。12 个已标记 `implemented` 的 executor（departure_text_parser, query_95306, create_wagon_shipments, shipment_status_sync 等）一个都没有被 live_service 串联执行。

## 2. 真实执行拓扑图

```
微信消息 (chat_records/*.jsonl)
    │
    ▼
WxOpsSourceWatcher.iter_message_events()   ← ✅ 轮询 chat_records
    │
    ▼
process_event_once()
    │
    ├─ _write_event_snapshot()             ← ✅ 写 runtime/events/
    ├─ match_message_event()               ← ✅ 匹配 monitoring_plan
    ├─ build_workflow_task_queue()         ← ✅ 生成 WorkflowTask 描述
    ├─ build_dashboard_payload_queue()     ← ✅ 生成 dashboard intents
    ├─ write_dashboard_payload_queue()     ← ✅ 写 runtime/dashboard_intents/
    ├─ build_dashboard_state_preview()     ← ✅ 生成 state preview
    ├─ write_dashboard_state_preview()     ← ✅ 写 runtime/dashboard_state/
    │
    └─ return {stats}                      ← ⚠️ 返回值被丢弃
         │
         ▼
    ┌─────────────────────────────────────────┐
    │          链条在此断裂                     │
    │                                         │
    │  ❌ parse_departure_text()    — 不被调用  │
    │  ❌ query_95306_waybills()    — 不被调用  │
    │  ❌ create_wagon_shipments()  — 不被调用  │
    │  ❌ shipment_status_sync()    — 不被调用  │
    │  ❌ refresh_dispatch_board()  — 不被调用  │
    │  ❌ sop_agent.db 写入         — 不发生     │
    └─────────────────────────────────────────┘
```

## 3. 证据：逐项验证

### 3.1 live_service 的 import

```python
# scripts/run_live_service.py, lines 33-39

from ops_hub.sop.dashboard_payload_queue import ...   # ✅ 生成预览
from ops_hub.sop.dashboard_state_preview import ...   # ✅ 生成预览
from ops_hub.sop.monitoring_plan_matcher import ...   # ✅ 匹配
from ops_hub.sop.monitoring_plan_preview import ...   # ✅ 加载 plan
from ops_hub.sop.sop_watcher import SopWatcher        # ✅ 热加载 SOP
from ops_hub.sop.source_watcher import ...            # ✅ 读取 chat_records
from ops_hub.sop.workflow_task import ...             # ✅ 生成任务描述
```

**以下模块从未被 import：**

```python
❌ from ops_hub.sop.sop_task_compiler import ...     # 仅在 status_command 中 import (L376)
❌ from ops_hub.sop.task_execution_registry import ... # 无任何引用
❌ from ops_hub.sop.departure_text_parser import ...   # 无任何引用
❌ from ops_hub.sop.query_95306_shipments import ...   # 无任何引用
❌ from ops_hub.sop.create_wagon_shipments import ...   # 无任何引用
❌ from ops_hub.sop.shipment_status_sync import ...     # 无任何引用
❌ from ops_hub.sop.enrich_release_batch import ...     # 无任何引用
```

**验证：** `grep -rn "TaskExecutionRegistry\|executor_runner\|parse_departure_text\|query_95306_shipments\|create_wagon_shipments" scripts/run_live_service.py` → 零结果。

### 3.2 process_event_once 的终点

```python
# scripts/run_live_service.py, lines 208-244

def process_event_once(...):
    event_path = _write_event_snapshot(event, ...)     # ① 写 event JSON
    match_result = match_message_event(event, ...)     # ② 匹配 plan
    workflow_queue = build_workflow_task_queue(...)     # ③ 生成任务描述

    payload_queue = build_dashboard_payload_queue(...)  # ④ 生成 intent
    payload_paths = write_dashboard_payload_queue(...)  # ⑤ 写 intent JSON

    preview = build_dashboard_state_preview(...)        # ⑥ 生成 state
    state_paths = write_dashboard_state_preview(...)    # ⑦ 写 state JSON

    return { ... }                                        # ⑧ 返回统计 → 被丢弃
```

`run_once()` 调用 `process_event_once()` 但不使用返回值：
```python
# line 275
process_event_once(event=event, monitoring_plan=monitoring_plan, ...)
# ↑ 返回值未赋值，直接丢弃
```

### 3.3 TaskExecutionRegistry 存在但无用

`task_execution_registry.py` 是唯一能调用 `parse_departure_text` 的模块：

```python
# task_execution_registry.py, line 24 & 116
from ops_hub.sop.departure_text_parser import parse_departure_text
...
candidate = parse_departure_text(event)  # ← 实际可执行
```

但它只在测试中被调用。`run_live_service.py` 中搜索 `TaskExecutionRegistry` → 零结果。

### 3.4 executor_runner 不存在

```
find /Users/qicai21/projects/repos -name "*executor*runner*" → 零结果
```

没有模块负责从 `WorkflowTask` 转换为实际的 executor 调用。

### 3.5 dispatch_board_data.json 不自动刷新

`refresh_dispatch_board()` 的调用点：

| 调用来源 | 触发条件 |
|----------|----------|
| `cli.py:_auto_refresh_dispatch_board` | 手动 CLI 命令（release_batch_ingested, formal_commit） |
| `cli.py:cmd_dispatch_board_serve` | HTTP server 定时器 |
| `scripts/generate_dispatch_board_data.py` | 手动脚本 |

**live_service 中搜索 `dispatch_board` → 零结果。**

新消息被 live_service 匹配后，`dispatch_board_data.json` 不会自动刷新。看板数据停留在最后一次手动刷新的快照。

### 3.6 DB 读写边界

| 操作 | 写目标 | 安全 |
|------|--------|------|
| `query_95306_shipments.py` | 只读 `mode=ro` | ✅ |
| `create_wagon_shipments.py` | `sop_agent.db` 仅 apply 模式 | ✅ |
| `enrich_release_batch.py` | `sop_agent.db` 仅 apply 模式 | ✅ |
| `shipment_status_sync.py` | `sop_agent.db` 仅 --apply | ✅ |
| `live_service` | 不碰任何 DB | ✅ |

95306_collection.sqlite3 的只读边界未被任何 executor 违反。所有写入仅发生在 sop_agent.db 且需要通过显式 apply/dry_run=False 参数。

## 4. 吉林金钢消息完整路径（从入群到停止）

以 seq=2206 "煤六 45节 四平铁 蓝鳍" 为例：

```
步骤  状态   位置                                 产出
─────────────────────────────────────────────────────────────
 1    ✅   WxOpsSourceWatcher                   读取 chat_records JSONL
 2    ✅   _write_event_snapshot                写 events/wx_2206.json
 3    ✅   match_message_event                 匹配到 jilin_jingang_jinzhou
 4    ✅   build_workflow_task_queue            WorkflowTask{status="planned"}
 5    ✅   build_dashboard_payload_queue        dashboard_intents/wx_2206.json
 6    ✅   build_dashboard_state_preview        dashboard_state/wx_2206.json
─── 链条在此断裂 ────────────────────────────────────────────
 7    ❌   parse_departure_text                未调用 → 无 DepartureCandidate
 8    ❌   query_95306_shipments_by_window     未调用 → 无 95306 查询结果
 9    ❌   create_wagon_shipments              未调用 → 无 wagon_shipments 写入
10    ❌   shipment_status_sync                未调用 → 无状态同步
11    ❌   refresh_dispatch_board              未调用 → 看板不刷新
```

**操作者视角：** 在群里发了一条消息 → 系统看到了 → 生成了匹配记录 → **到此为止**。后续的"解析发车信息 → 查 95306 → 创建车次 → 同步状态 → 刷新看板"全都没有发生。

## 5. 已注册但未使用的 executor（jilin_jingang）

`sop_task_compiler.py` `_EXECUTOR_STATUS` 中标记为 `implemented` 的 12 个 action：

| action | 实现文件 | live_service 调用？ |
|--------|----------|---------------------|
| parse_departure_text | departure_text_parser.py | ❌ |
| classify_message | monitoring_plan_matcher.py | ✅ (match_message_event) |
| extract_release_notice_json | runner.py | ❌ |
| identify_project | monitoring_plan_matcher.py | ✅ (fallback) |
| create_release_batch | data_agent/agent.py | ❌ |
| update_release_batch_fields | data_agent/agent.py | ❌ |
| query_release_batches | data_agent/agent.py | ❌ |
| extract_freight_detail | freight_detail_extractor.py | ❌ |
| enrich_release_batch | enrich_release_batch.py | ❌ |
| build_time_window | shipment_query_window.py | ❌ |
| query_95306_waybills | query_95306_shipments.py | ❌ |
| poll_shipment_snapshots | shipment_status_sync.py | ❌ |
| create_wagon_shipments | create_wagon_shipments.py | ❌ |
| bind_wagons_to_release_batch | create_wagon_shipments.py | ❌ |
| check_existing_wagon_shipments | create_wagon_shipments.py | ❌ |

**live_service 实际调用的 executor：2/15**（仅 classify_message 和 identify_project 的 fallback 匹配逻辑）。

## 6. 根因分析

缺少一个模块连接两个已经独立工作的系统：

```
已工作的系统 A:                      已工作的系统 B:
live_service                        executor scripts
  ├─ 检测消息 ✅                      ├─ parse_departure_text ✅
  ├─ 匹配项目 ✅                      ├─ query_95306_waybills ✅
  ├─ 生成任务描述 ✅                   ├─ create_wagon_shipments ✅
  └─ 写预览 JSON ✅                   └─ shipment_status_sync ✅

          缺少连接器（executor_runner）
          ├─ 读取 WorkflowTask
          ├─ 解析 flow 类型 → 选择 executor 链
          ├─ 调用 executor（dry_run → apply）
          └─ 触发 dispatch_board 刷新
```

`TaskExecutionRegistry` 已经具备了部分连接能力（能根据消息内容判断 flow 类型并调用 `parse_departure_text`），但它从未被集成到 live_service 主循环中。

## 7. 改动文件

- 无代码改动（纯审计任务）

## 8. Git

- branch: codex/sop-real-sop-topology-audit-20260525
- commit (baseline): adffb54
- PR: 无（审计任务，不涉及代码变更）
