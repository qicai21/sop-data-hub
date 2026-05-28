# R30: 吉林金钢 canonical SOP v0.2 implementation gap audit

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**SOP File:** `config/project_sops/jilin_jingang.yaml`
**SOP Commit:** `734b800`
**SOP Version:** `v0.2`
**SOP SHA256:** `34e4828fb38448c51de18569626228eadef88a5cc72b60d521895ba7ae397fe8`
**Audit Type:** 只读，不改代码

---

## 一、Git Log

```
63741fb R30: add GitHub audit summary
117ba10 R30: 吉林金钢 canonical SOP v0.2 implementation gap audit
734b800 docs: update canonical jilin jingang SOP yaml
094da92 R28: 吉林金钢 SOP 制单后链路核查 — WAIT_95306_CONFIRM is the SOP endpoint
5f9ab7b R27: canonical SOP source migration — config/project_sops/ (YAML, git-tracked)
```

---

## 二、live_service --status

```json
{
  "sop_runtime": {
    "source_of_truth": "git",
    "sop_dir": ".../config/project_sops",
    "loaded_projects": ["chaoyang_steel", "", "jiusan", "zhongtang_special_steel"],
    "sop_hash": "12d10ac88920bf70",
    "file_hashes": {
      "jilin_jingang.yaml": "34e4828fb38448c5"
    }
  }
}
```

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|:--:|
| `source_of_truth` | `git` | `git` | ✅ |
| `sop_dir` | `config/project_sops` | `config/project_sops` | ✅ |
| `loaded_projects` contains `jilin_jingang_jinzhou` | yes | **NO** — 显示为空字符串 `""` | ❌ |

空字符串 `""` 就是 `jilin_jingang.yaml` 被解析后的 project_id。YAML 文件本身被 hot-reload 跟踪（hash `34e4828fb38448c5` 存在于 `file_hashes`），但 project_id 丢失。

---

## 三、核心问题：YAML 结构不兼容

### 3.1 证据

```
YAML 顶层键: project, scope, groups, sources, entities, states, flows, runtime, exceptions, archive, acceptance

load_project_sop() 读取:
  data.get("project_id", "")     → None  → ""
  data.get("project_name", "")   → None  → ""
  data.get("listening_tasks", []) → None  → []

SOP 实际结构:
  project.id        → "jilin_jingang_jinzhou"
  project.name      → "吉林金钢（锦州港→四平）铁矿发运项目"
  groups (array)    → 3 groups with roles + message_types
  flows (dict)      → release_notice_flow, freight_detail_flow, departure_flow, tracking_flow
```

### 3.2 根因

`load_project_sop()` (line 339) 期望**扁平 YAML 结构**：

```python
project_id=data.get("project_id", "")  # 顶层键
```

但新版 SOP v0.2 使用**嵌套结构**：

```yaml
project:
  id: jilin_jingang_jinzhou
  name: ...
```

同理，`listening_tasks` 不存在 — SOP 用 `groups` + `flows` 替代。`load_project_sop()` 返回 `listening_tasks=[]`，下游 `_project_sop_yaml_to_compiler_input` 无数据可迭代。

**结果：SOP 文件被 watcher 跟踪，但解析后 project_id 为空，listening_tasks 为空，不产生任何 monitoring plan 节点。**

---

## 四、实现缺口矩阵

### Q1: 新版 jilin_jingang.yaml 是否能被当前 SOPWatcher 正确读取？

**不能。** YAML 文件被跟踪（`file_hashes` 中有记录），hash 变化会触发 reload。但 `load_project_sop()` 解析后 `project_id=""`，`listening_tasks=[]`。下游编译器和匹配器拿不到任何有效数据。

### Q2: loaded_projects 是否包含 jilin_jingang_jinzhou？

**不包含。** 显示 `""` 空字符串。

---

### A. release_notice_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| detect_release_notice | 部分实现 | `monitoring_plan_matcher.py` | `match_message_event()` | — | image 消息走 `_match_document_item`，但 jilin_jingang 的 monitoring plan 为空（YAML 解析失败），无法触发匹配 |
| identify_project | 部分实现 | `monitoring_plan_matcher.py` | `_fallback_alignment_match()` | — | GROUP001+"四平"→jilin_jingang_jinzhou 硬编码 fallback，但不走 monitoring plan |
| find_existing_release_batch | 部分实现 | `agent.py:315` | `ingest_release_batch()` → `ON CONFLICT(batch_key)` | `release_batches` | 按 `batch_key` hash 去重，SOP 要求按 `order_identifier`+`contract_no`+`ship_name`+`destination` 匹配；`order_identifier` 字段不存在 |
| create_or_update_release_batch | 已实现 | `agent.py:315` | `ingest_release_batch()` | `release_batches` | — |
| update_existing_release_batch | 已实现 | `agent.py:315` | `ON CONFLICT DO UPDATE` | `release_batches` | — |

### B. freight_detail_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| GROUP005/GROUP013 识别 | 未实现 | — | — | — | `_fallback_alignment_match` 只有 GROUP001/003 分支，无 GROUP005/013 |
| order_identifier 提取 | 未实现 | — | — | — | 字段不存在于 `release_batches` |
| contract_no 提取 | 未实现 | — | — | `release_batches` | 字段存在但无提取逻辑 |
| cargo_name_detail 提取 | 未实现 | — | — | — | 字段不存在 |
| enrich_release_batch | 未实现 | — | — | — | 无 `enrich_release_batch()` 函数 |

### C. departure_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| parse_departure_text | 未实现 | — | — | — | 无 regex/parser 处理"6道，四平铁，46车" |
| message_time 提取 | 未实现 | — | — | — | — |
| destination 提取 | 未实现 | — | — | — | — |
| car_count 提取 | 未实现 | — | — | — | — |
| build_time_window(±60m) | 未实现 | — | — | — | 当前 window 基于 `ticketed_at`(reconciler)，非 `message_time` |
| query_95306_waybills | 未实现 | — | — | — | 无比 message_time 驱动的查询 |
| extract_wagon_no | 部分实现 | `gen_jljg_excel.py:27` | 只读已有 `wagon_shipments` | `wagon_shipments` | 从 95306 自动提取不存在 |
| extract_container_no | 未实现 | — | — | — | 表无此字段；`gen_jljg_excel.py` 硬编码"待补" |
| extract_waybill_no | 未实现 | — | — | — | 表无此字段 |
| check_existing_wagon_shipments | 部分实现 | `inspection_95306_reconciler.py` | `_dedupe_planned_rows()` | `shipment_release_batch_matches` | 只在 reconciler 的去重逻辑中，非独立步骤 |
| bind_wagons_to_release_batch | 部分实现 | `inspection_95306_reconciler.py` | `_formal_row_from_shipment()` | `shipment_release_batch_matches` | 通过 reconciler，非 SOP departure_flow 路径 |
| create_wagon_shipments | 未实现 | — | — | `wagon_shipments` | 表存在但无代码写入（当前数据为手动导入） |
| generate_departure_excel_task | 部分实现 | `gen_jljg_excel.py` | 独立脚本 | `wagon_shipments` | 硬编码 ship_name/contract_no/order_id；非 task 驱动 |
| generate_factory_transport_json_task | 未实现 | `gen_jljg_excel.py:68-83` | print-only preview | — | 硬编码，非生产级 |
| dry_run_receiver_system | 未实现 | — | — | — | 无 HTTP client |
| send_excel_task (郭东北) | 未实现 | — | — | `report_tasks` | 表存在但无代码写入 jilin_jingang 记录 |
| telegram_json_delivery_task | 未实现 | — | — | — | — |

### D. tracking_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| poll_shipment_snapshots | 未实现 | — | — | — | 无 95306 定时轮询机制 |
| 发车/到站/交付 识别 | 未实现 | — | — | `shipments` | 95306 DB 有 `latest_stage_name` 字段，但无代码读取状态变化 |
| update_dashboard_state | 未实现 | `dispatch_board.py` | `render_dispatch_board()` | — | 只生成静态 HTML，无反写 |
| update_wagon_arrival_status | 未实现 | — | — | `wagon_shipments` | 表有 `arrived_at` 但无代码更新 |
| update_dispatch_status | 部分实现 | `agent.py` | `update_release_dispatch_status()` | `release_batches` | 仅 CLI 手动调用，非自动 |
| mark_confirmed_received | 未实现 | — | — | — | 无此函数 |
| close_dashboard_state | 未实现 | — | — | — | — |

### E. runtime.task_resolver

| Task Type | 状态 | 文件 | 缺口 |
|-----------|:----:|------|------|
| `excel_generation` | 未实现 | — | 无统一 TaskResolver；`gen_jljg_excel.py` 是独立硬编码脚本 |
| `json_generation` | 未实现 | — | 无实现 |
| `telegram_delivery` | 未实现 | — | 无实现；`delivery_result.py` 只有 `simulate_delivery_result()` |
| `http_delivery` | 未实现 | — | 无实现 |

**散落脚本：**
- `gen_jljg_excel.py` — 独立 Excel 生成，硬编码 ship_name/contract_no/order_id/container_no
- `report_intent.py` — 本地 report 意图解析，不实际生成/发送
- `delivery_result.py` — 模拟交付结果，不执行真实发送

### F. 数据库支撑

#### release_batches

| SOP 字段 | DB 列 | 存在 | 备注 |
|----------|-------|:---:|------|
| id | id | ✅ | |
| ship_name | ship_name | ✅ | |
| destination | destination_station | ✅ | |
| quantity | batch_quantity | ✅ | |
| cargo_name | cargo_name | ✅ | |
| order_identifier | — | ❌ | 不存在 |
| contract_no | contract_no | ✅ | |
| cargo_name_detail | — | ❌ | 不存在 |
| dispatch_status | dispatch_status | 部分 | 仅 in_progress/completed/suspended/cancelled |
| arrival_status | — | ❌ | 不存在 |
| delivered_at | — | ❌ | 不存在 |
| confirmed_received_at | — | ❌ | 不存在 |

#### wagon_shipments

| SOP 字段 | DB 列 | 存在 | 备注 |
|----------|-------|:---:|------|
| wagon_no | car_no | ✅ | |
| waybill_no | — | ❌ | 不存在 |
| container_no | — | ❌ | 不存在 |
| release_batch_id | batch_id | ✅ | FK |
| departed_at | departed_at | ✅ | |
| arrived_at | arrived_at | ✅ | |
| delivered_at | — | ❌ | 不存在 |
| confirmed_received | — | ❌ | 不存在 |

#### shipment_release_batch_matches

存在于 95306 SQLite DB（非 sop_agent.db）。由 `inspection_95306_reconciler.py` 创建和管理。Schema：`id`, `release_batch_id`, `shipment_ydid`, `shipment_car_no`, `ticketed_at`, `status_code`, `status_name`, `latest_stage_name`, `latest_event_time`。

---

## 五、P0/P1/P2 缺口清单

### P0 — 主链无法跑通

| # | 节点 | 缺口 |
|---|------|------|
| P0-1 | YAML 解析器 | `load_project_sop()` 读取扁平 `project_id`，SOP v0.2 使用嵌套 `project.id`；`listening_tasks` 不存在。**阻塞所有下游。** |
| P0-2 | freight_detail_flow (全部) | GROUP005/013 无路由，无字段提取，无 `enrich_release_batch` |
| P0-3 | departure_flow — parse_departure_text | 无发运文本解析器（"6道，四平铁，46车"） |
| P0-4 | departure_flow — 95306 查询 | 无 message_time 驱动的 ±60m 查询窗口 |
| P0-5 | departure_flow — create_wagon_shipments | 表存在但无代码写入 |
| P0-6 | tracking_flow — poll_shipment_snapshots | 无 95306 定时轮询 |
| P0-7 | DB — 缺失字段 | `release_batches`: order_identifier, cargo_name_detail；`wagon_shipments`: container_no, waybill_no |
| P0-8 | DB — tracking 字段 | `release_batches`: arrival_status, delivered_at, confirmed_received_at；`wagon_shipments`: delivered_at |
| P0-9 | task_resolver (全部) | excel_generation, json_generation, telegram_delivery, http_delivery 无实现 |

### P1 — 可人工兜底

| # | 节点 | 缺口 |
|---|------|------|
| P1-1 | find_existing_release_batch | 按 batch_key hash 去重，非 order_identifier+contract_no 复合匹配 |
| P1-2 | departure — Excel 生成 | `gen_jljg_excel.py` 硬编码，需数据驱动 |
| P1-3 | departure — factory JSON | 无生产级 JSON 生成 |
| P1-4 | departure — dry-run / delivery | 无 HTTP/TG 发送 |
| P1-5 | tracking — 状态映射 | 无 95306 latest_stage_name → SOP 状态 映射 |
| P1-6 | tracking — update_dashboard_state | dispatch_board 只读，无反写 |
| P1-7 | tracking — mark_confirmed_received | 无自动/手动确认机制 |
| P1-8 | departure — send_excel_task | `report_tasks` 表存在但无代码写入 |

### P2 — 体验优化

| # | 节点 | 缺口 |
|---|------|------|
| P2-1 | release_notice — extract_release_notice_json | 无独立 JSON 输出节点 |
| P2-2 | departure — optional_ship_name | 未从发运文本提取船名 |
| P2-3 | tracking — close_dashboard_state | 无闭环 |
| P2-4 | exceptions — ocr_incomplete | runner.py 有异常处理但非 SOP 感知 |
| P2-5 | exceptions — shipment_count_mismatch | 无 count 不匹配手动确认 |

---

## 六、回答问题

### Q3: 为什么长航滨海仍然 in_progress？

1. `release_batches.dispatch_status` 创建时默认 `"in_progress"`。
2. 无 95306 轮询机制读取 `shipment_release_batch_matches.status_name` 变化。
3. 无代码将 95306 的"发车/到站/交付"映射到 `dispatch_status`。
4. `confirmed_received` 状态不存在于任何表中。

### Q4: 推进到 confirmed_received 最少需要哪些 P0 功能？

最少 3 个 P0：

1. **P0-1** YAML 解析器修复 — 否则没有任何 SOP 指令生效
2. **P0-6** `poll_shipment_snapshots` — 定时查询 95306 状态
3. **P0-8** DB tracking 字段 — `confirmed_received_at`, `delivered_at`

加上 P1-5（状态映射）、P1-7（mark_confirmed_received）可完成自动化确认。

### Q5: 哪些已有脚本可以复用？

1. **`inspection_95306_reconciler.py`** — `_query_all_shipments_in_window()` 可复用于 departure_flow 的 95306 查询窗口
2. **`release_match_spec.py`** — `build_match_spec()` / `row_matches_spec()` 可复用于识别 release_batch
3. **`agent.py`** — `ingest_release_batch()` 可复用 create_or_update
4. **`gen_jljg_excel.py`** — Excel 格式可作模板参考（需拆除硬编码）
5. **`dispatch_board.py`** — HTML 渲染可复用 dashboard 展示
6. **`monitoring_plan_matcher.py`** — `_fallback_alignment_match()` 的 keyword routing 模式可扩展

### Q6: 哪些硬编码必须拆除？

| 文件 | 硬编码 | 应替换为 |
|------|--------|---------|
| `gen_jljg_excel.py:50` | `'JGCG-SFY-HTNK20260501'` | 从 `release_batches.contract_no` 读取 |
| `gen_jljg_excel.py:51` | `'CGR20260518174420'` | 从 `release_batches` 读取（需新增 order_identifier 列） |
| `gen_jljg_excel.py:54` | `'待补-需从95306API提取箱号'` | 95306 自动提取 |
| `gen_jljg_excel.py:58` | `'长航滨海'` | 从 `release_batches.ship_name` 读取 |
| `gen_jljg_excel.py:74` | 整个 factory JSON 结构硬编码 | 数据驱动模板 |
| `monitoring_plan_matcher.py:96-99` | `'四平铁矿箱', '四平放货', '四平'` | 应从 compiled monitoring plan 读取 |

---

## 七、下一轮建议（不执行）

1. **修复 `load_project_sop()`** — 支持嵌套 `project.id` 结构，或迁移 YAML 为扁平格式
2. **编译 SOP groups → listening_tasks** — 将 SOP 的 `groups` + `flows` 结构转换为 runtime 可用的 `listening_tasks`
3. **添加 DB 列** — `order_identifier`, `cargo_name_detail`, `container_no`, `waybill_no`, tracking 字段
4. **实现 departure text parser** — regex 提取 message_time/destination/car_count
5. **实现 tracking poller** — 周期性 95306 状态查询
6. **实现 TaskResolver** — 从 `gen_jljg_excel.py` 重构为 `excel_generation` 服务
7. **拆除 gen_jljg_excel.py 硬编码** — 数据驱动

**优先级顺序：** P0-1 → P0-7(P0-8 并列) → P0-3/P0-4 → P0-5 → P0-6 → P0-9 → P1 items → P2 items

---

**审计完成。零代码变更。零 SOP 修改。**
