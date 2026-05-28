# R38: wx-ops-agent report sender audit — 修正 R37 capability map

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Audit Type:** 只读 — 不开发、不发送、不修改外部 repo

---

## R37 capability map 原结论

R37 将 `report-sender` 评级为 **🔴 无**，原文：

> "零真实发送能力"
> "仅 `report_intent.py` / `delivery_result.py` — 都是 dry-run 模拟"

## 业务判断

**该结论不准确。** 外部发送能力存在且经过生产验证，问题在于 `sop-data-hub` 尚未集成。

---

## 审计范围

| 资源 | 路径 |
|------|------|
| wx-ops-agent repo | `/Users/qicai21/projects/repos/wx-ops-agent` |
| wx-ui-bridge | `/Users/qicai21/projects/ai-tools/mcp/wx-ui-bridge` |
| Hermes skills | `~/.hermes/skills/devops/wx-ui-bridge-group-image-send/` |
| sop-data-hub (当前) | `/Users/qicai21/projects/repos/sop-data-hub` |

---

## Part A: 现有发送能力清单

### A1: wx-ui-bridge — 底层发送引擎

wx-ui-bridge 通过 macOS Accessibility API 驱动微信桌面客户端。

| 能力 | 函数 | 文件 | 输入参数 | 输出/返回 | 可复用 | 风险/限制 |
|------|------|------|---------|----------|:----:|---------|
| **打开聊天** | `build_open_chat_workflow(target)` | `wx_bridge/composites/workflows.py:36` | target: str (群搜索名或联系人) | list[UIAction] | ✅ | 需要微信在前台 |
| **发送文本** | `build_send_text_workflow(target, content)` | `wx_bridge/composites/workflows.py:40` | target + content: str | list[UIAction] | ✅ | 文本内容无长度限制文档 |
| **发送文件** | `build_send_file_workflow(target, path)` | `wx_bridge/composites/workflows.py:47` | target + path: str (本地文件路径) | list[UIAction] | ✅ | 需要文件已存在于磁盘 |
| **发送图片** | `build_send_image_workflow(target, path)` | `wx_bridge/composites/workflows.py:54` | target + path: str | list[UIAction] | ✅ | 发送后自动 reset→minimize |
| **队列发送** | `mcp.enqueue_message()` + `executor.process_batch()` | `sop-post-commit-report-sending.md` | contact_id, content, attachments | DeliveryTrace {result: "success"} | ✅ | 需要 `PYTHONPATH=.` + cd 到 bridge 目录 |
| **队列确认** | `queue.json` → `status: "sent"` | `wx_bridge/storage` | queue file path | JSON trace | ✅ | 需手动读取 queue.json |

### A2: wx-ops-agent — 上层集成封装

| 能力 | 函数 | 文件 | 输入参数 | 输出/返回 | 可复用 | 风险/限制 |
|------|------|------|---------|----------|:----:|---------|
| **发送图片** | `send_image_via_bridge(target, image_path)` | `integrations/wx_bridge.py:11` | target + image_path | bool (成功/失败) | ✅ | 仅支持图片，不支持文件/文本 |
| **处理图片(非发送)** | `process_image_via_ops_data_hub()` | `integrations/ops_data_hub.py:18` | image_path | status + record_ids | ❌ | 图片分析，非发送 |
| **处理文本(非发送)** | `process_text_via_ops_data_hub()` | `integrations/ops_data_hub.py:60` | text | status + record_count | ❌ | 文本摄入，非发送 |

### A3: Hermes skill — 已验证发送模式

| 能力 | 来源 | 状态 |
|------|------|:--:|
| **发送文件到群** | `wx-ui-bridge-group-image-send` SKILL.md | ✅ 已验证 |
| **发送图片到群** | 同上 | ✅ 已验证 |
| **发送到联系人** | 同上 — target 用中文名（如 `郭东北`） | ✅ 已验证 |
| **发送到群** | 同上 — target 用 `[GROUPXXX]` | ✅ 已验证 |
| **发送后确认** | `duplicate-send-prevention.md` 参考 | ✅ 有经验 |
| **发送后防重复** | `sop-post-commit-report-sending.md` | ✅ 有模式 |

### A4: 群组/联系人映射 (entity_mapping.json + tracking_rules.yaml)

| 标识 | 名称 | wxid | 类型 | 可用于发送 |
|------|------|------|------|:--:|
| `[GROUP001]` | 铁晟业务工作群 | 18919596289@chatroom | 群组 | ✅ |
| `[GROUP002]` | 铁晟业务数据统计群（内部） | 19344923463@chatroom | 群组 | ✅ |
| `[GROUP003]` | 中唐特钢发运群 | 38739555499@chatroom | 群组 | ✅ |
| `[GROUP013]` | 数据单发群 | 56327225598@chatroom | 群组 | ✅ |
| `[GROUP102]` | 龙虾测试群 | 56842512942@chatroom | 群组 | ✅ |
| `郭东北` | 郭东北 | dongbei9234 | 联系人 | ✅ |
| `[GROUP005]` | (SOP 引用但未注册) | 未注册 | 待注册 | ❌ |
| 门思岐 | 门思岐 | wxid_vv7wvx2np0w522 | 联系人 | ✅ |

### A5: sop-data-hub 内部的 dry-run 设施

| 能力 | 文件 | 实际行为 | 状态 |
|------|------|---------|:--:|
| `ReportIntent` | `sop/report_intent.py` | 建模报告意图（excel/json/telegram/http） | dry-run |
| `DeliveryResult` | `sop/delivery_result.py` | 模拟交付结果 | dry-run |
| `LifecycleCloseout` | `sop/delivery_result.py` | 模拟闭环 | dry-run |
| `DashboardPayload` | `sop/dashboard_payload_queue.py` | 写 JSON 到 runtime/dashboard_intents/ | local file |
| `generated_excel_artifact` | `task_execution_registry.py` | 注册为 dry_run_only | — |
| `generate_departure_excel` | SOP task_resolver | task_type: "excel_generation" | dry_run_only |
| `generate_factory_json` | SOP task_resolver | task_type: "json_generation" | dry_run_only |
| `telegram_json_delivery` | SOP task_resolver | task_type: "telegram_delivery" | dry_run_only |
| `receiver_upload` | SOP task_resolver | task_type: "http_delivery" | dry_run_only |

**结论：** sop-data-hub 有完整的**意图建模**（ReportIntent + DeliveryResult + task_resolver），但零**实际发送**——全部标记为 `dry_run_only`。

### A6: Telegram 发送能力

| 能力 | 提供者 | 状态 |
|------|--------|:--:|
| Hermes `send_message` tool | Hermes agent (当前会话) | ✅ 可直接用于 Telegram DM |
| `telegram:` target | `send_message(action='list')` | ✅ Home channel ID 8660960857 |

---

## Part B: sop-data-hub 如何调用外部发送能力

### B1: sop-data-hub 是否应直接调用 wx-ops-agent 的脚本？

**否。** 原因：

1. wx-ops-agent 的 `send_image_via_bridge()` 仅支持图片，不支持 Excel 文件或 JSON。
2. wx-ops-agent 是一个独立运行的服务（daemon），sop-data-hub 不应对其进程有假设。
3. 底层发送能力实际在 **wx-ui-bridge**，wx-ops-agent 和 sop-data-hub 都应对等调用。

**推荐架构：**

```
sop-data-hub
    └── src/ops_hub/report_sender/
           ├── wx_bridge_adapter.py   ← 调用 wx-ui-bridge 发送
           └── telegram_adapter.py    ← 调用 Hermes send_message 或直接 HTTP
```

而不是：

```
sop-data-hub → (import) wx-ops-agent → wx_bridge.py → wx-ui-bridge
```

### B2: 是否需要通过 skill / CLI / API 间接调用？

**推荐 CLI（subprocess）方式**，原因：

1. wx-ui-bridge 需要 `PYTHONPATH=.` + 特定 CWD
2. 已验证的调用模式已是 subprocess（`sop-post-commit-report-sending.md`）
3. 避免循环导入（wx-ops-agent 已 import ops-data-hub）
4. 简单、可观测、可重试

替代方案（API）未来可行但当前过度设计。

### B3: 是否需要新增 report_sender_adapter？

**是。** 建议新增 `src/ops_hub/report_sender/` 包，包含：

| 模块 | 职责 |
|------|------|
| `wx_bridge_adapter.py` | 封装对 wx-ui-bridge 的调用：生成临时 Python 脚本、执行 subprocess、解析返回值 |
| `telegram_adapter.py` | 封装 Telegram 发送（Hermes send_message 或直接 HTTP） |
| `report_sender.py` | 统一入口：读取 `ReportIntent` → 选择 adapter → 执行发送 → 返回 `DeliveryResult` |
| `sender_registry.py` | `task_resolver` 映射：`excel_generation` → wx_bridge_adapter, `telegram_delivery` → telegram_adapter 等 |

### B4: 发送 Excel 给测试联系人郭东北，需要哪些参数？

```
target = "郭东北"           # WeChat 联系人显示名
file_path = "/path/to/departure.xlsx"  # 已生成的文件路径
method = "wx_bridge"        # 通过 wx-ui-bridge 发送
```

SOP YAML 中已定义：

```yaml
# jilin_jingang.yaml L373-377
- node: "send_test_excel"
  runtime_rules:
    test_mode:
      receiver: "郭东北"
  action:
    - "send_excel_task"
```

`receiver: "郭东北"` 可直接映射到 `entity_mapping.json` 中的 `search_name: "郭东北"`。

### B5: 发送 JSON 到 Telegram dry-run，需要哪些参数？

```
target = "telegram"         # 使用 Hermes send_message
content = json.dumps(data, ensure_ascii=False, indent=2)
method = "telegram"          # 通过 Hermes send_message 工具
```

SOP YAML 中已定义：

```yaml
# jilin_jingang.yaml L378-382
- node: "telegram_json_delivery"
  runtime_rules:
    test_mode: true
  action:
    - "telegram_json_delivery_task"
```

### B6: 发送成功后如何确认？

**wx-ui-bridge 方式：**

1. `executor.process_batch()` → 检查 `queue.json` 中 `status: "sent"` + `delivery_trace[].result: "success"`
2. 或检查 subprocess 返回值 + stdout 中的 `click dispatched`

**Telegram 方式：**

1. Hermes `send_message` 工具返回成功即确认

**统一确认模型：**

`DeliveryResult` 已有 `confirmation_ref` 字段，可填入 queue.json trace 中的 `delivery_trace[].attempt_id` 或 telegram message_id。

### B7: 失败后如何记录待办？

`delivery_result.py` 中的 `_todo_from_failure()` 已实现此逻辑：

```python
# status: "delivery_failed"
# category: "delivery_failed"
# suggested_action: "retry the simulated delivery"
```

只需将 `simulate_delivery_result()` 替换为真实 `execute_delivery()` 即可复用现有 todo 链路。

---

## Part C: 修正 R37 capability map

### 原结论（R37）

> **6. report-sender** | **🔴 无** | 零真实发送能力 | P0 — 阻塞发运报告/JSON 输出

### 修正结论（R38）

**report-sender: 🟡 外部能力存在，sop-data-hub 未集成**

| 维度 | 原评级 | 修正评级 | 事实依据 |
|------|:--:|:--:|------|
| wx-ui-bridge 底层发送 | 🔴 无 | 🟢 已实现且验证 | `build_send_file_workflow`, `build_send_text_workflow`, queue-based delivery |
| wx-ops-agent 封装 | 🔴 无 | 🟡 部分（仅图片） | `send_image_via_bridge()` |
| Hermes skill 已验证 | 🔴 无 | 🟢 已验证生产模式 | `wx-ui-bridge-group-image-send` SKILL.md |
| sop-data-hub 集成 | 🔴 无 | 🔴 无 | 全部 dry_run_only |
| **综合评级** | 🔴 无 | 🟡 外部能力存在，sop-data-hub 未集成 | P1（非 P0 — 有人工兜底路径） |

**优先级从 P0 降至 P1 的理由：**

1. 外部发送能力已验证可用（不是零能力）
2. 存在人工兜底路径：郭东北可以通过 Hermes agent 手动发送
3. P0 应留给真正阻塞主链的任务（DB schema migration, 95306 poller 等）
4. 集成工作量可控（~200-300 行 adapter，复用已验证模式）

---

## Part D: 当前缺口量化

### sop-data-hub 中 4 个 delivery task 的状态

| Task name | executor_status | SOP 定义 | 外部能力 |
|-----------|:--:|------|:--:|
| `send_excel_task` | dry_run_only | `send_test_excel` → 郭东北 | wx-ui-bridge `build_send_file_workflow` ✅ |
| `telegram_json_delivery_task` | dry_run_only | `telegram_json_delivery` → Telegram | Hermes `send_message` ✅ |
| `generate_departure_excel_task` | prototype | `generate_departure_excel` | 有散落脚本但非 SOP 驱动 |
| `generate_factory_transport_json_task` | prototype | `generate_factory_json` | 无实现 |

### 需新增的 adapter

| Adapter | 封装对象 | 输入 | 输出 | 优先级 |
|---------|---------|------|------|:--:|
| `wx_bridge_adapter.py` | wx-ui-bridge subprocess | target, file_path | DeliveryResult | P1 |
| `telegram_adapter.py` | Hermes send_message | content, format | DeliveryResult | P1 |
| `report_sender.py` | 统一入口 | ReportIntent | DeliveryResult | P1 |
| `sender_registry.py` | task_resolver 映射 | task_type → adapter | str | P1 |

---

## Part E: 下一轮建议

### 建议路线图

| 轮次 | 任务 | 优先级 |
|:--:|------|:--:|
| R39 | 实现 `src/ops_hub/report_sender/wx_bridge_adapter.py` — 基于已验证的 subprocess 模式封装 wx-ui-bridge 文件/文本发送 | P1 |
| R40 | 实现 `src/ops_hub/report_sender/telegram_adapter.py` — 复用 Hermes send_message 或直接 Telegram Bot API | P1 |
| R41 | 实现 `report_sender.py` + `sender_registry.py` — 将 `task_resolver` 中 4 个 dry_run_only task 连接到真实 adapter | P1 |
| R42 | 端到端测试：发运文本触发 → 生成 Excel → 发送给郭东北 → 确认 | P1 |
| R— | **在完成上述之前**，应优先处理 P0 缺口：DB schema migration → enrich_release_batch → 95306 定时轮询 → confirmed_received | P0 |

### 不得提前执行

- 本轮仅审计。R39+ 需用户明确指令。
- 不得修改 wx-ops-agent 或 wx-ui-bridge。
- 不得发送任何真实消息。

---

## 验证

```bash
cd /Users/qicai21/projects/repos/sop-data-hub
python scripts/run_live_service.py --status
pytest tests/functional -v
```

---

## 修正记录

| 版本 | 日期 | 变更 |
|------|------|------|
| R37 | 2026-05-28 | 原始 capability map — report-sender 标记为 🔴 无 |
| R38 | 2026-05-28 | **本审计** — 修正为 🟡 外部能力存在/sop-data-hub 未集成；详细列出现有发送能力；建议后续集成路线 |
