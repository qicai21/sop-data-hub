# Report: R43 — SOP Data Hub Phase Review and Readiness Scoring

| 字段 | 内容 |
|------|------|
| Order | R43 |
| 执行日期 | 2026-05-29 |
| Branch | codex/sop-real-sop-topology-audit-20260525 |
| Commits | R36 → R42（14 commits） |
| 测试 | 176 passed, 0 failed |

---

## 1. 总体评分（0-5）

| # | 维度 | 评分 | 理由 |
|---|------|:--:|------|
| 1 | SOP compiler / task compiler | **4** | 完整 pipeline：YAML → monitoring_plan → task_plan → trace。但 executor_status registry 滞后（R40-R42 新增 executor 未注册） |
| 2 | wx-tracker / message intake | **3** | GROUP001/005/013 消息路由完整，但缺消息去重、缺 GROUP005/013 fallback 分支 |
| 3 | freight detail extraction | **4** | R40 extractor + R41 manual bind executor 完整，覆盖 3 类文本格式 |
| 4 | departure text parsing | **4** | R40 parser 覆盖 8 种已知格式，handles Chinese quotes and ship names |
| 5 | shared 95306 query | **3** | R42 QueryWindow + query_95306_shipments_by_window 只读实现完成；但未与 departure_flow 集成、未写入 executor_status registry |
| 6 | create_wagon_shipments | **0** | **未实现。** SOP departure_flow 上最关键的一环。无任何代码 |
| 7 | shipment status sync / tracking | **3** | R36 手动 sync 完成（蓝鳍 18/18），R39 添加 delivered_at/confirmed_received_at 列。缺自动化轮询、缺 auto confirmed_received 逻辑 |
| 8 | DB schema and idempotency | **4** | R39 + R41 完成 11 列 schema migration。幂等设计贯穿释放 notice 和 release_batch update |
| 9 | dashboard_state progression | **1** | DashboardPayloadQueue 和 StatePreview 可预览。但无 auto 状态推进，缺 release_batch → in_progress/dispatched/arrived/delivered/confirmed_received 自动流转 |
| 10 | report generation Excel / JSON | **1** | 只有硬编码 prototype（gen_jljg_excel.py）。无 data-driven 生成 |
| 11 | report sender adapter | **1** | 有 delivery_result.simulate_delivery_result 本地空壳。无真实 WeChat/Telegram/HTTP 发送 |
| 12 | scheduler / live_service integration | **2** | live_service bootstrap 可单次执行，有 status 命令。但无 cron job 注册、无持续轮询 |
| 13 | contract intelligence | **0** | 未实现。合同要素 NLP 抽取、contract → SOP 联动均为空 |
| 14 | multi-project readiness | **1** | 只有 jilin_jingang 有完整 flows。chaoyang.yaml 无 flows 段（仅 listening_tasks）；zhongtang/jiusan 同理 |

**综合评分：2.1 / 5.0**

---

## 2. 已完成能力（A）

| 能力 | 文件 | 自 R38 以来 |
|------|------|:--:|
| SOP YAML → ExecutableTaskPlan 编译器 | sop_task_compiler.py | R34 |
| SOPWatcher hot reload | sop_watcher.py | R32 |
| MessageEvent 匹配（text + image） | monitoring_plan_matcher.py | R32 |
| RawAssetBundle（image + ocr + metadata） | raw_asset_bundle.py | R32 |
| freight_detail_extractor | freight_detail_extractor.py | R40 ✨ |
| departure_text_parser | departure_text_parser.py | R40 ✨ |
| enrich_release_batch executor | enrich_release_batch.py | R41 ✨ |
| 95306 query window executor | query_95306_shipments.py + shipment_query_window.py | R42 ✨ |
| shipment_status_sync (manual CLI) | shipment_status_sync.py | R36 |
| DB schema migration (11 新列) | db.py + enrich_release_batch.py | R39 + R41 ✨ |
| TaskExecutionRegistry + trace | task_execution_registry.py | R35 |
| DashboardPayloadQueue + StatePreview | dashboard_*.py | R30 |
| live_service status | run_live_service.py | — |
| 蓝鳍 18/18 车状态同步至 delivered | — | R36 |

---

## 3. Prototype 能力（B）

| 能力 | 文件 | 缺口 |
|------|------|------|
| Excel 生成 | scripts/gen_jljg_excel.py | 硬编码数值，非 data-driven |
| Factory JSON 生成 | scripts/gen_jljg_excel.py | print-only preview |
| 报告发送 | delivery_result.py | 只有 simulate，无真实发送 |
| 检装车-95306 reconciler | business_query.py | CLI 手动执行，plan/commit 模式 |

---

## 4. Missing Executor（C）— departure_flow 依然是最缺的

| # | 节点 | 动作 | 优先级 |
|---|------|------|:--:|
| C-1 | `build_95306_query_window` | 从 DepartureCandidate.message_time 构造 ±N min 窗口 | ⚠️ 代码已实现（R42 QueryWindow），但未注册 executor_status |
| C-2 | `query_95306_waybills` | 用窗口查询 95306 shipments | ⚠️ 代码已实现（R42），但未注册 executor_status |
| C-3 | `extract_wagons_and_waybills` | extract_wagon_no / container_no / waybill_no | P0 |
| C-4 | `deduplicate_departure_records` | 检查已有 wagon_shipments | P0 |
| C-5 | `match_release_batch` | bind_wagons_to_release_batch | P0 |
| C-6 | `write_departure_records` | **create_wagon_shipments** 🔴 | P0 |
| C-7 | `poll_shipment_snapshots` | 定时轮询 95306 | P0 |
| C-8 | `mark_confirmed_received` | auto/manual 确认收货 | P0 |

**关键：R42 实现的 QueryWindow 和 query_95306_shipments_by_window 未被 SOP executor_status registry 收录**，所以 live_service 报告的 task 统计中它们仍显示为 missing。这是一个注册表不同步的问题，不是 capability gap。

---

## 5. 阻塞自动运行的 P0（D）

| # | P0 阻塞项 | 影响 | 估计轮次 |
|---|-----------|------|:--:|
| D-1 | **create_wagon_shipments** | departure_flow 核心：发运文本 → 95306 查询 → wagon_shipments.write。缺此节点，吉林金钢无法自动创建任何运单行 | 1 |
| D-2 | **executor_status registry 同步** | R40-R42 已实现的 executor 未注册，导致 task plan 报告误导性 missing | 0.5 |
| D-3 | **定时 95306 轮询** | tracking_flow 无法自动触发。当前只能手动 CLI | 0.5 |
| D-4 | **confirmed_received 自动推进** | R39 已添加 confirmed_received_at 列，但无自动写入逻辑 | 0.5 |
| D-5 | **report generation (data-driven)** | 当前 Excel/JSON 是硬编码 prototype，无法用于正式业务 | 1 |
| D-6 | **report delivery (telegram/wechat)** | delivery_result 只有 local simulate | 1 |
| D-7 | **消息去重** | group_id + seq 幂等未实现，重复消息会触发重复处理 | 0.5 |
| D-8 | **dashboard_state 自动推进** | SOP 定义了 9 个 release_batch 状态，但无自动流转代码 | 1 |

---

## 6. 可人工补发验收的项目（E）

| 项目 | 可验收程度 | 说明 |
|------|:--:|------|
| **吉林金钢** | 部分可验收 | release_notice_flow 全链路可 run（OCR → create release_batch）。freight_detail_flow 可 run（extract → enrich）。departure_flow 阻塞于 create_wagon_shipments。tracking_flow 阻塞于自动化轮询 |
| **朝阳钢铁** | 不可验收 | YAML 无 flows 段，无任何 executor 注册。当前只能监听 GROUP001 图片 → create_release_batch |
| **中唐特钢** | 不可验收 | YAML 无 flows 段。只能监听 GROUP003 文本/图片 |
| **九三大豆** | 不可验收 | YAML 无 flows 段 |

---

## 7. 不建议现在验收的项目（F）

- **朝阳钢铁**：缺 flows 段，且发运报告格式不同于吉林金钢（朝钢报告需防冻标记列、无合同号→船名自动绑定的需求不同）
- **中唐特钢**：缺 flows 段，检装车通知单处理与吉林金钢有差异
- **九三大豆**：完全不同品类（大豆 vs 矿粉），95306 查询参数不同

---

## 8. 吉林金钢现在能否自动处理

| 子链路 | 可否自动 | 阻塞 |
|--------|:--:|------|
| 货运信息补充（freight_detail → enrich） | ✅ 可 | dry_run → plan 可；apply 需人工指定 release_batch_id |
| 发运文本（departure_text → parse） | ✅ 可 | parse_departure_text 完成 |
| 95306 查询（QueryWindow → 95306） | ✅ 可 | R42 query_95306_shipments_by_window 只读完成 |
| **wagon_shipments 创建** | ❌ 不可 | create_wagon_shipments 未实现 |
| 状态同步（95306 → wagon_shipments） | 部分 | 手动 CLI 可；缺自动化 |
| dashboard 更新 | ❌ 不可 | 无 auto 状态推进 |

**结论：吉林金钢可以走到 "parse departure text → 查 95306 → 输出候选列表" 这一步。但还不能自动创建 wagon_shipments 行，不能自动推进状态。**

---

## 9. 朝阳钢铁是否适合现在补全 SOP

### 当前 chaoyang.yaml 程度

```yaml
project_id: "chaoyang_steel"
status: "active"
# 只有 listening_tasks，无 flows 段
listening_tasks:
  - GROUP001: create_release_batch (image) + process_inspection_slip (image)
```

**完全没有 flows 段**。SOPTaskCompiler 只能输出 0 task。

### 与吉林金钢共用哪些 executor

| Executor | 共用 | 备注 |
|----------|:--:|------|
| classify_message | ✅ | 共用 |
| extract_release_notice_json | ✅ | 共用 OCR pipeline |
| create_release_batch | ✅ | 共用 agent.ingest_release_batch |
| parse_departure_text | ⚠️ | 需要添加朝钢 ship names（木森17/合远9 已在 known_ships），但 destination 需映射到 朝阳西 |
| query_95306_shipments_by_window | ✅ | 完全共用 |
| create_wagon_shipments | ✅ | 实现后共用 |
| shipment_status_sync | ✅ | 完全共用 |
| Excel 生成 | ❌ | 朝钢报告格式不同（5列 vs 吉林金钢格式） |

### 缺哪些朝钢专用规则

1. **没有 flows 段** — 需要编写 departure_flow / tracking_flow
2. **出发文本解析** — 四平→朝阳西 destination 映射
3. **已知 ship names** — 合远9、朝钢专有船名
4. **Excel 报告格式** — `chaogang_53cars(2).xlsx` 5列格式（rowNo|铁路车号|货票时间|发站名称|防冻标记）
5. **report_targets** — production group_id 待确认
6. **检装车通知单处理** — 朝钢有 process_inspection_slip 节点，不同于吉林金钢的纯发运文本

### 建议

**暂不建议补全朝钢 flows 段。** 集中力量完成吉林金钢的 create_wagon_shipments → 自动化打通。打通后再横展到朝钢。

---

## 10. 木森17 是否适合现在补发验收

### 当前状态

- `departure_text_parser.py` 已注册 `木森17` 为已知 ship_name
- 95306 DB 中是否有木森17 数据需要查询确认
- 系统当前能走到 `parse departure text → build time window → query 95306 → output candidates`

### 如果补发，系统能走到哪一步

```
departure_text → parse_departure_text ✅
                → build_95306_query_window ✅ (QueryWindow)
                → query_95306_waybills ✅ (query_95306_shipments_by_window)
                → extract wagons ✅ (candidates 有 wagon_no/container_no)
                → deduplicate ❌ (no check_existing_wagon_shipments)
                → match_release_batch ❌ (no bind_wagons_to_release_batch)
                → create_wagon_shipments ❌ (not implemented)
                → generate_excel ⚠️ (prototype only)
                → send_report ❌ (no real delivery)
```

### 会卡在哪一步

卡在 `create_wagon_shipments`。补发的价值有限。

### 建议

**不建议现在补发木森17。** 等 create_wagon_shipments 实现后，一次性验收蓝鳍/长航滨海/木森17 三条链路的 departure_flow。

---

## 11. 离真正自动运行还差几轮

### 保守估计

| 轮次 | 内容 | 估算 |
|:--:|------|:--:|
| R43 | 阶段复盘（本次） | ✅ done |
| R44 | executor_status registry 同步 → 修正 task plan 统计 | 0.5 轮 |
| R45 | create_wagon_shipments executor（核心 P0） | 1 |
| R46 | 消息去重 + GROUP005/013 fallback | 0.5 |
| R47 | 定时 95306 轮询 cron + confirmed_received auto | 0.5 |
| R48 | data-driven Excel + JSON generation | 1 |
| R49 | report delivery (telegram) | 0.5 |
| R50 | dashboard_state auto progression | 0.5 |
| R51 | 集成测试：蓝鳍/长航滨海/木森17 全链路验收 | 0.5 |

**保守估计：还需要 5 轮才能达到吉林金钢单项目自动运行。**

### 最短可验收路径

如果聚焦到 "解析发运文本 → 建 wagon_shipments → 手动查状态" 的 минимальный 闭环：

| 轮次 | 内容 |
|:--:|------|
| R44 | executor_status registry 同步 + 补齐 `extract_freight_detail` / `query_95306_waybills` 注册 |
| R45 | **create_wagon_shipments** executor |
| R46 | 测试：蓝鳍 departure text → 95306 query → create wagon_shipments → 验证 DB 写入 |

**最短 2 轮可验收 departure_flow 的 create → verify 闭环。**

---

## 12. 修正：executor_status registry 不同步问题

R40-R42 实现了以下 executor，但 `_EXECUTOR_STATUS` dict 未更新：

| 实际实现 | 文件 | registry 中状态 |
|----------|------|:--:|
| `parse_departure_text` | departure_text_parser.py | implemented ✅ |
| `extract_freight_detail` | freight_detail_extractor.py | **missing** ❌ |
| `enrich_release_batch` | enrich_release_batch.py | **missing** ❌ |
| `query_95306_waybills` | query_95306_shipments.py | **missing** ❌ |
| `build_time_window` | shipment_query_window.py (QueryWindow) | **missing** ❌ |

这导致 `live_service --status` 报告的 5 implemented / 16 missing / 4 prototype 不准确。**实际 completed executor 比 report 多 4 个。** R44 应优先修正。

---

## 13. 附：当前测试状况

```
176 passed, 0 failed in ~2s
```

所有测试包括 R36-R42 的 functional 测试通过，无回归。
