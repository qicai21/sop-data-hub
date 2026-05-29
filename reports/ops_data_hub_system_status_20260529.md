# ops-data-hub 系统现状报告

**日期：** 2026-05-29  
**分支：** `codex/sop-real-sop-topology-audit-20260525`  
**报告人：** local Hermes (model: deepseek-v4-pro)

---

## 1. 总体评估

ops-data-hub 的 SOP 基础设施在 **吉林金钢 departure_flow** 上已具备完整能力（文本→95306→建车→写库），但存在 **三个结构性缺口** 导致系统无法形成自动闭环：

| # | 缺口 | 严重程度 | 影响 |
|---|------|----------|------|
| 1 | 匹配层与执行层脱节 | 🔴 P0 | 微信消息能识别但不能自动处理 |
| 2 | 朝阳钢铁 inspection_notice_flow 无 executor | 🔴 P0 | 检装车通知单无法自动处理 |
| 3 | 看板数据未实时联动 | 🟡 P1 | 新消息刷新后看板无变化 |

---

## 2. 今日发现的问题清单

### 2.1 live_service 中断两天

```
最后日志: 2026-05-27 17:01:41
用户消息: 2026-05-29 14:58 "煤六 45节 四平铁 蓝鳍" (seq=2206)
          2026-05-29 15:11 "四平放货蓝鳍3000吨"       (seq=2209)
服务重启: 2026-05-29 15:57 (手动)
```

中间 46 小时无进程运行，微信消息在此期间无法被处理。

**根因：** live_service 进程（PID 75931）在 May 27 后已退出，无自动重启机制。进程通过 `python scripts/run_live_service.py` 手动启动，退出后不会自愈。

### 2.2 SOPTaskCompiler 硬编码 flow name

`src/ops_hub/sop/sop_task_compiler.py:330`

```python
# before (hardcoded)
for flow_name in ("release_notice_flow", "freight_detail_flow", "departure_flow", "tracking_flow"):

# after (fixed in R49)
for flow_name in yaml_flows:
```

**影响：** 朝阳钢铁 5 个 flow 中只有 2 个被编译，`inspection_notice_flow`（核心流程）被跳过。

**状态：** ✅ 已在 commit `adffb54` 中修复。

### 2.3 代码/YAML 双副本不同步

ops-data-hub 实际存在两套文件：

| 路径 | 角色 |
|------|------|
| `/Users/qicai21/projects/repos/src/` | 主仓库源码 |
| `/Users/qicai21/projects/repos/sop-data-hub/src/` | live_service 运行时实际加载的源码 |
| `/Users/qicai21/projects/repos/config/project_sops/` | 主仓库 YAML |
| `/Users/qicai21/projects/repos/sop-data-hub/config/project_sops/` | live_service 实际读取的 YAML |

`chaoyang.yaml` 的 sop-data-hub 副本在 commit `9ea2b4e` 后未更新，仍是旧版 stub 文件（无 flows 定义）。R49 中已手动同步。

**风险：** 未来 git pull 不会自动更新 `sop-data-hub/` 子目录（未被 git track），需手动维护。

### 2.4 匹配层与执行层脱节（核心问题）

```
live_service 做的事情：
  match_message_event           ✅ 消息→项目→SOP节点匹配
  build_workflow_task_queue     ✅ 生成任务描述
  build_dashboard_payload_queue ✅ 写 intent JSON 到 runtime/dashboard_intents/
  build_dashboard_state_preview ✅ 写 state JSON 到 runtime/dashboard_state/
  ─────────────────────────────────────────────────────────────────
  departure_text_parser         ❌ 从未调用
  query_95306_shipments         ❌ 从未调用
  create_wagon_shipments        ❌ 从未调用
  sop_agent.db 写入             ❌ 从未调用
```

**结果：**
- 微信消息被正确识别（wx_2206→"四平铁矿箱"→jilin_jingang_jinzhou）✅
- dashboard_intents 和 dashboard_state 被正确写入 ✅
- 但 `sop_agent.db` 没有任何新数据，release_batch 无更新 ❌
- 用户仍需要手动跑 departure dry-run / apply

**谁在做执行？** 当前只有**人工触发**的脚本：
- `scripts/create_wagon_shipments.py`
- `scripts/enrich_release_batch.py`
- `scripts/query_95306_shipments.py`

这些脚本没有集成到 live_service pipeline 中。

### 2.5 看板数据源不是实时的

| 数据层 | 来源 | 刷新方式 |
|--------|------|----------|
| `runtime/dashboard_state/` | live_service 实时写 | 消息到达时自动 |
| `runtime/dashboard_intents/` | live_service 实时写 | 消息到达时自动 |
| `dispatch_board_data.json` | **从 DB 读取** | 需手动跑 `generate_dispatch_board_data.py` |
| `dispatch_board.html` | **从 JSON 渲染** | 需 HTTP 服务重启或用 🔄 按钮 |

新消息到达时，live_service 写入了 intents/states，但看板页面读取的是 `dispatch_board_data.json`（今天 11:38 的缓存），用户看到的是 2+ 小时前的快照。

### 2.6 朝阳钢铁 17 个 missing executor（R48 发现）

chaoyang 的 `inspection_notice_flow` 需要 13 个 action，**全部缺失**：

| node | 缺失 action |
|------|-----------|
| detect_inspection_notice | `extract_inspection_notice_json` |
| extract_inspection_notice_fields | `extract_ship_name`, `extract_destination_station`, `extract_cargo_name`, `extract_car_count`, `extract_wagon_numbers`, `extract_notice_time` |
| match_release_batch | `match_release_batch_by_ship_destination_cargo` |
| query_95306_by_inspection_notice | `query_95306_shipments_by_window_or_wagon_numbers` |

加上 tracking_flow 的 5 个 + 其他，共 17 个 missing。

---

## 3. 架构分析

```
                    当前架构                          理想架构
                    ────────                          ────────

 微信消息 ──→ source_watcher (轮询 chat_records)      同左 ✅
                │
                ▼
           match_message_event (匹配项目/SOP节点)       同左 ✅
                │
                ▼
           workflow_task_queue (生成任务描述)          同左 ✅
                │
                ▼
           dashboard intents/states (写预览)           同左 ✅
                │
                ▼
           ╔═══════════════════════╗
           ║    ████████████████   ║              ┌─────────────────┐
           ║    ██  缺 口  ████   ║              │ executor_runner │
           ║    ████████████████   ║              │  (不存在)        │
           ╚═══════════════════════╝              │                 │
                                                  │ departure_text  │
                                                  │   → parser      │
                                                  │   → query_95306 │
                                                  │   → create      │
                                                  │     wagons      │
                                                  │   → write DB    │
                                                  └─────────────────┘
                                                          │
                                                          ▼
           dispatch_board_data.json (手动生成)        看板自动刷新
           dispatch_board.html (手动刷新)
```

**缺失的模块：`executor_runner`** — 一个在 live_service 匹配完成后，自动调用对应 SOP executor 链的执行器。

---

## 4. 数据流总结

| 步骤 | 当前状态 | 触发方式 |
|------|----------|----------|
| 1. wx-ops-agent 写 chat_records | ✅ 自动 | daemon 持续运行 |
| 2. source_watcher 发现新消息 | ✅ 自动 | live_service 运行时 |
| 3. 消息匹配到项目/SOP节点 | ✅ 自动 | live_service |
| 4. 写 dashboard intents/states | ✅ 自动 | live_service |
| 5. **执行 SOP executor 链** | ❌ **不存在** | 需人工跑脚本 |
| 6. 刷新看板数据 | ❌ 非实时 | 需手动 run generate_dispatch_board_data.py |
| 7. 朝阳检装车处理 | ❌ 全 absent | 17 executors missing |

---

## 5. 建议修复优先级

### P0 — 必须立即修复

1. **添加 `executor_runner` 到 live_service pipeline**
   - 在 `process_event_once` 中，当 workflow_tasks 包含 `departure_flow` 时，自动调 `departure_text_parser` → `query_95306` → `create_wagon_shipments`
   - 默认 dry-run 模式，人工 confirm 后 apply
   - 或直接 auto-apply 当 confidence 足够高

2. **实现 inspection_notice_extractor**
   - 新增 `src/ops_hub/sop/inspection_notice_extractor.py`
   - 从检装车通知单 OCR JSON 中提取 ship_name / destination_station / cargo_name / car_count / wagon_numbers

3. **添加 live_service 进程守护**
   - 通过 cron 或 launchd 确保进程崩溃后自动重启
   - 或通过心跳检测（已有 `--status` 命令可做检测脚本）

### P1 — 近期

4. **看板数据自动刷新**
   - `process_event_once` 匹配成功且 executor 执行后，自动调 `generate_dispatch_board_data.py`

5. **统一源码路径**
   - 消除 `sop-data-hub/` 子目录的代码/YAML 副本
   - 或将 `sop-data-hub/` 纳入 git track

---

## 6. 当前实际可用能力（诚实评估）

| 能力 | 吉林金钢 | 朝阳钢铁 |
|------|----------|----------|
| 识别微信消息 | ✅ | ✅ (classify_message) |
| 匹配项目/SOP节点 | ✅ | ✅ |
| 解析 departure_text | ✅ (需手动) | N/A (用检装车图) |
| 解析检装车通知单 | N/A | ❌ |
| 查询 95306 | ✅ (需手动) | ❌ |
| 创建 wagon_shipments | ✅ (需手动) | ❌ (executor 已实现但上游未就绪) |
| tracking/sync | ✅ (需手动) | ❌ |
| 生成报表 | 🟡 prototype | 🟡 prototype |
| 发送微信 | 🟡 dry_run_only | 🟡 dry_run_only |

**诚实结论：** 吉林金钢的完整链路目前依赖**人工逐步执行**，每个节点都能用但需要手动触发。朝阳钢铁尚无可用的自动化节点。系统检测和匹配能力已经具备，但执行层缺失。

---

## 7. 改动记录

| commit | 日期 | 内容 |
|--------|------|------|
| `adffb54` | 2026-05-29 | R49: SOPTaskCompiler 动态编译 — 修复 chaoyang 2→5 flows |
| `6173d7e` | 2026-05-29 | R48: chaoyang SOP compile audit 报告 |
| `9ea2b4e` | 2026-05-29 | (用户) chaoyang.yaml 更新（含 inspection_notice_flow） |
