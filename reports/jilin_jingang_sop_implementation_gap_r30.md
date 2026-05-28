# R30: 吉林金钢 canonical SOP v0.2 implementation gap audit (兼容版)

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**SOP Commit:** `5411674` — parser-compatible YAML
**SOP Version:** `v0.2` (兼容版)
**SOP SHA256:** `84be49be23f4729dce57e3fa29554a68d77e0cd66db272313c20ea38005d20c1`
**Audit Type:** 只读

---

## 一、Git Log

```
5411674 docs: make jilin jingang SOP yaml parser compatible
4f3ea71 R30: regenerate — 吉林金钢 SOP v0.2 gap audit with YAML structure evidence
63741fb R30: add GitHub audit summary
117ba10 R30: 吉林金钢 canonical SOP v0.2 implementation gap audit
734b800 docs: update canonical jilin jingang SOP yaml
```

---

## 二、live_service --status 确认

```json
{
  "sop_runtime": {
    "source_of_truth": "git",
    "sop_dir": ".../config/project_sops",
    "loaded_projects": [
      "chaoyang_steel",
      "jilin_jingang_jinzhou",
      "jiusan",
      "zhongtang_special_steel"
    ],
    "sop_hash": "0ff420db615b7e9c",
    "file_hashes": {
      "jilin_jingang.yaml": "84be49be23f4729d"
    }
  }
}
```

| 检查项 | 预期 | 实际 | 结果 |
|--------|------|------|:--:|
| `source_of_truth` | `git` | `git` | ✅ |
| `sop_dir` | `config/project_sops` | `config/project_sops` | ✅ |
| `loaded_projects` contains `jilin_jingang_jinzhou` | yes | yes | ✅ |
| YAML 解析 | flat keys | flat keys | ✅ |
| `listening_tasks` | 3 groups | 3 groups, 9 routes | ✅ |

**P0-1 (YAML 解析器不兼容) 已解决。**

---

## 三、monitoring_plan 编译验证

```
GROUP001: watch_items=1
  image → doc=出港计划通知单 → detect_release_notice
  (text→match_departure_text_template → SKIPPED by compiler — relies on fallback)

GROUP005: watch_items=3
  image → doc=出港计划通知单 → detect_release_notice
  text → msg_type=freight_detail_text → enrich_release_batch
  file → departure_excel → submit_or_hold_delivery

GROUP013: watch_items=3
  image → doc=出港计划通知单 → detect_release_notice
  text → msg_type=freight_detail_text → enrich_release_batch
  file → departure_excel_test → submit_or_hold_delivery
  (text→match_departure_text_template → SKIPPED by compiler — relies on fallback)
```

### 编译层面的缺口

**P0-new: `freight_detail_text` 匹配失效。** 编译器将 `trigger_condition: "freight_detail_text"` 映射为 `message_type: "freight_detail_text"`。但 matcher 的 `_match_text_item()` 用 `message_type in event_text` 做匹配 — 实际消息文本中不含字面字符串 `"freight_detail_text"`。text_patterns 为空 `[]`，无法匹配任何真实消息。消息最终落入 `_fallback_alignment_match`，但 GROUP005/013 无 fallback 分支。

**P0-new: `match_departure_text_template` 被跳过。** 编译器在 GROUP001 和 GROUP013 的 `match_departure_text_template` text routing 上执行 `continue`（line 218-223 of sop_watcher.py），依赖 fallback matcher。fallback 中 GROUP001 "四平" 关键词匹配有效，但 GROUP013 无分支。

---

## 四、实现缺口矩阵

### A. release_notice_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| detect_release_notice | 部分实现 | `monitoring_plan_matcher.py` | `match_message_event()` | — | monitoring plan 正确编译 3 个 group 的 image→detect_release_notice；但 `extract_release_notice_json` 无独立节点 |
| identify_project | 部分实现 | `monitoring_plan_matcher.py` | `_fallback_alignment_match()` | — | GROUP001 fallback 支持"四平"→jilin_jingang_jinzhou |
| find_existing_release_batch | 部分实现 | `agent.py:315` | `ingest_release_batch()` | `release_batches` | `batch_key` hash 去重，非 `order_identifier`+`contract_no` 匹配 |
| create_or_update_release_batch | 已实现 | `agent.py:315` | `ingest_release_batch()` | `release_batches` | — |
| update_existing_release_batch | 已实现 | `agent.py:315` | `ON CONFLICT DO UPDATE` | `release_batches` | — |

### B. freight_detail_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| GROUP005/GROUP013 消息匹配 | **编译正常，匹配失效** | `monitoring_plan_matcher.py` | `_match_text_item()` | — | `message_type="freight_detail_text"` 永远不匹配真实消息文本；text_patterns 为空 |
| extract order_identifier | 未实现 | — | — | — | 字段不存在于 `release_batches` |
| extract contract_no | 未实现 | — | — | `release_batches` | 字段存在但无提取逻辑 |
| extract cargo_name_detail | 未实现 | — | — | — | 字段不存在 |
| enrich_release_batch | 未实现 | — | — | — | 无 `enrich_release_batch()` 函数 |

### C. departure_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| detect_departure_message | 未实现 | — | — | — | fallback 匹配有效(GROUP001)，但无 parser |
| parse_departure_text | 未实现 | — | — | — | 无 regex/parser 处理"6道，四平铁，46车" |
| message_time 提取 | 未实现 | — | — | — | — |
| destination 提取 | 未实现 | — | — | — | — |
| car_count 提取 | 未实现 | — | — | — | — |
| build_time_window(±60m) | 未实现 | — | — | — | 当前 window 基于 `ticketed_at`(reconciler)，非 `message_time` |
| query_95306_waybills | 未实现 | — | — | — | — |
| extract_wagon_no | 部分实现 | `gen_jljg_excel.py:27` | 只读已有的 `wagon_shipments` | `wagon_shipments` | 从 95306 自动提取不存在 |
| extract_container_no | 未实现 | — | — | — | 表无此字段 |
| extract_waybill_no | 未实现 | — | — | — | 表无此字段 |
| deduplicate_departure_records | 部分实现 | `inspection_95306_reconciler.py` | `_dedupe_planned_rows()` | `shipment_release_batch_matches` | 只在 reconciler 中，非独立 |
| bind_wagons_to_release_batch | 部分实现 | `inspection_95306_reconciler.py` | `_formal_row_from_shipment()` | `shipment_release_batch_matches` | 通过 reconciler，非 SOP departure_flow |
| create_wagon_shipments | 未实现 | — | — | `wagon_shipments` | 表存在但无代码写入 |
| generate_departure_excel | 部分实现 | `gen_jljg_excel.py` | 独立脚本 | `wagon_shipments` | 硬编码 ship_name/contract_no/order_id/container_no |
| generate_factory_json | 未实现 | `gen_jljg_excel.py:68-83` | print-only preview | — | 硬编码 |
| dry_run_receiver_system | 未实现 | — | — | — | 无 HTTP client |
| send_test_excel (郭东北) | 未实现 | — | — | `report_tasks` | 表存在但无代码写入 |
| telegram_json_delivery | 未实现 | — | — | — | — |

### D. tracking_flow

| SOP 节点 | 状态 | 文件 | 函数 | 表 | 缺口 |
|----------|:----:|------|------|-----|------|
| poll_shipment_snapshots | 未实现 | — | — | — | 无 95306 轮询 |
| 发车/到站/交付 识别 | 未实现 | — | — | `shipments` | 95306 DB 有 `latest_stage_name` 但无代码读取 |
| update_dashboard_state | 未实现 | `dispatch_board.py` | `render_dispatch_board()` | — | 只生成静态 HTML |
| update_wagon_arrival_status | 未实现 | — | — | `wagon_shipments` | 表有 `arrived_at` 但无代码更新 |
| update_dispatch_status | 部分实现 | `agent.py` | `update_release_dispatch_status()` | `release_batches` | CLI 手动，非自动 |
| mark_confirmed_received | 未实现 | — | — | — | — |
| close_dashboard_state | 未实现 | — | — | — | — |

### E. runtime.task_resolver

| Task Type | 状态 | 文件 | 缺口 |
|-----------|:----:|------|------|
| `excel_generation` | 未实现 | `gen_jljg_excel.py`(硬编码) | 无统一 TaskResolver |
| `json_generation` | 未实现 | — | — |
| `telegram_delivery` | 未实现 | — | — |
| `http_delivery` | 未实现 | — | — |

### F. 数据库支撑

`release_batches`:
| SOP 字段 | DB 列 | 存在 |
|----------|-------|:---:|
| order_identifier | — | ❌ |
| contract_no | contract_no | ✅ |
| cargo_name_detail | — | ❌ |
| arrival_status | — | ❌ |
| delivered_at | — | ❌ |
| confirmed_received_at | — | ❌ |

`wagon_shipments`:
| SOP 字段 | DB 列 | 存在 |
|----------|-------|:---:|
| wagon_no | car_no | ✅ |
| waybill_no | — | ❌ |
| container_no | — | ❌ |
| delivered_at | — | ❌ |
| confirmed_received | — | ❌ |

---

## 五、P0/P1/P2 缺口清单

### P0 — 主链无法跑通

| # | 节点 | 缺口 |
|---|------|------|
| P0-2 | freight_detail — 消息匹配失效 | `message_type="freight_detail_text"` 不匹配真实文本；需添加 text_patterns 或调整匹配逻辑 |
| P0-3 | departure — 发运文本解析 | 无 parser 处理"6道，四平铁，46车" |
| P0-4 | departure — 95306 查询 | 无 message_time 驱动的 ±60m 窗口 |
| P0-5 | departure — write wagon_shipments | 表存在但无代码写入 |
| P0-6 | tracking — poll_shipment_snapshots | 无 95306 轮询 |
| P0-7 | DB — 业务字段 | `release_batches`: order_identifier, cargo_name_detail；`wagon_shipments`: container_no, waybill_no |
| P0-8 | DB — tracking 字段 | `release_batches`: arrival_status, delivered_at, confirmed_received_at；`wagon_shipments`: delivered_at |
| P0-9 | task_resolver | excel/json/telegram/http — 零实现 |

### P1 — 可人工兜底

| # | 节点 | 缺口 |
|---|------|------|
| P1-1 | find_existing_release_batch | batch_key hash，非 order_identifier+contract_no 复合 |
| P1-2 | departure — Excel/JSON 生成 | `gen_jljg_excel.py` 硬编码 |
| P1-3 | departure — delivery | 无 HTTP/TG 发送 |
| P1-4 | tracking — 状态映射 | 无 95306→SOP 状态映射 |
| P1-5 | tracking — update_dashboard_state | dispatch_board 只读 |
| P1-6 | tracking — mark_confirmed_received | 无确认机制 |
| P1-7 | detect_release_notice — extract_release_notice_json | 无独立 JSON 输出 |
| P1-8 | GROUP013 发运文本 fallback | `_fallback_alignment_match` 无 GROUP013 分支 |

### P2 — 体验优化

| # | 节点 | 缺口 |
|---|------|------|
| P2-1 | departure — optional_ship_name | 未从发运文本提取 |
| P2-2 | tracking — close_dashboard_state | 无闭环 |
| P2-3 | exceptions — ocr_incomplete | runner.py 有异常但非 SOP 感知 |
| P2-4 | exceptions — shipment_count_mismatch | 无 count 不匹配确认 |
| P2-5 | acceptance 规则自动化 | 9 条 acceptance 规则无自动校验 |

---

## 六、回答问题

### 为什么长航滨海仍然 in_progress？

1. `dispatch_status` 默认 `"in_progress"`，创建时无后续状态推进。
2. 无 95306 轮询机制。
3. 无 `confirmed_received` 逻辑。
4. DB 缺少 `confirmed_received_at` 字段。

### 推进到 confirmed_received 最少需要哪些 P0？

最少 3 个：**P0-6** (poll_shipment_snapshots) + **P0-8** (DB tracking 字段) + 状态映射逻辑（P1-4）。

### 哪些已有代码可复用？

- `inspection_95306_reconciler.py` — `_query_all_shipments_in_window()` 可复用于 95306 查询
- `release_match_spec.py` — 匹配逻辑复用
- `agent.py` — `ingest_release_batch()` 复用
- `gen_jljg_excel.py` — Excel 格式模板（需拆除硬编码）
- `dispatch_board.py` — HTML 渲染复用
- `monitoring_plan_matcher.py` — keyword routing 模式可扩展 GROUP005/013

### 硬编码清单

| 文件 | 硬编码值 | 替换目标 |
|------|---------|---------|
| `gen_jljg_excel.py:50` | `'JGCG-SFY-HTNK20260501'` | `release_batches.contract_no` |
| `gen_jljg_excel.py:51` | `'CGR20260518174420'` | `release_batches.order_identifier`（新字段） |
| `gen_jljg_excel.py:54` | `'待补-需从95306API提取箱号'` | 95306 自动提取 |
| `gen_jljg_excel.py:58` | `'长航滨海'` | `release_batches.ship_name` |
| `gen_jljg_excel.py:74-81` | 工厂 JSON 结构 | 数据驱动模板 |

---

## 七、YAML 兼容性变更总结

| 项目 | 旧版 (734b800) | 新版 (5411674) | 状态 |
|------|---------------|---------------|:--:|
| project_id | 嵌套 `project.id` | 扁平 `project_id` | ✅ |
| project_name | 嵌套 `project.name` | 扁平 `project_name` | ✅ |
| listening_tasks | 不存在 | 3 groups, 9 routes | ✅ |
| loaded_projects | `""` | `"jilin_jingang_jinzhou"` | ✅ |

---

## 八、下一轮建议（不执行）

1. **修复 freight_detail_text 匹配** — 添加 text_patterns 或改为 keyword-based 匹配
2. **添加 DB 列** — order_identifier, cargo_name_detail, container_no, waybill_no, tracking 字段
3. **实现发运文本解析器** — regex 提取 message_time/destination/car_count
4. **实现 tracking poller** — 周期性 95306 查询
5. **实现 TaskResolver** — 从 gen_jljg_excel.py 重构
6. **拆除硬编码**
7. **扩展 fallback matcher** — GROUP005/013 支持

**优先级：** P0-2 → P0-7/P0-8 → P0-3/P0-4 → P0-5 → P0-6 → P0-9 → P1 items

---

**审计完成。零代码变更。零 SOP 修改。**
