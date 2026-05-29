# 普通货运看板验收报告 R47

**日期**: 2026-05-29
**branch**: `codex/sop-real-sop-topology-audit-20260525`
**看板类型**: 放货/发运/图片识别/匹配入库看板 (ordinary freight dispatch board)

---

## 1. 看板能否打开？

**✅ 可以打开。**

- **本地服务器**: `http://127.0.0.1:8765/dispatch_board.html`
- **数据源**: `dashboard/dispatch_board_data.json`（由 `refresh_dispatch_board()` 从 DB 生成）
- **HTML 模板**: `dashboard/dispatch_board.html`
- **启动命令**: `ops-hub dispatch-board serve`
- **刷新命令**: `ops-hub dispatch-board refresh --reason manual`

---

## 2. 看板数据来源

### Part A: 数据源确认

| 问题 | 答案 |
|---|---|
| 使用静态 JSON、runtime/dashboard_state，还是直接读 DB？ | **直接读 DB** → 输出静态 JSON (`dispatch_board_data.json`) |
| 数据生成脚本 | `src/ops_hub/data_agent/dispatch_board.py` → `refresh_dispatch_board()` |
| 是否需要重新生成？ | ✅ 已重新生成（`reason=manual_acceptance`） |

**数据流**:
```
sop_agent.db        ──read──→  release_batches, contracts, text_pending 等
95306 DB (ro)       ──read──→  shipment_release_batch_matches (formal wagons)
                            ↓
                   dispatch_board_data.json
                            ↓
                   dispatch_board.html (fetch JSON)
```

### 关键架构发现

`formal_wagon_count` (= "已正式入库车数") 的数据来源是 **95306 DB 的 `shipment_release_batch_matches`** 表（由 `inspection_95306_reconciler` 写入），**不是** `sop_agent.db` 的 `wagon_shipments` 表。

---

## 3. DB vs Dashboard 对比

### 3.1 蓝鳍

| 指标 | DB 实际值 | Dashboard 显示值 | 一致？ |
|---|---|---|---|
| lot01 release_batch_id | `916ec02...47d310` | 同 | ✅ |
| lot01 ship_name | 蓝鳍 | 蓝鳍 | ✅ |
| lot01 batch_sequence | lot01 | lot01 | ✅ |
| lot01 cargo_name | 铁矿 | **印粉** ⚠️ | ❌ |
| lot01 batch_quantity | 3000 | 3000 | ✅ |
| lot01 contract_no | HNMC20260520-1X-1 | HNMC20260520-1X-1 | ✅ |
| lot01 plan_id | CGR20260520095954 | CGR20260520095954 | ✅ |
| lot01 dispatch_status | completed | **已发完** | ✅ |
| lot01 actual_wagon_count | **46** | **18** ⚠️ | ❌ |
| lot01 confirmed_received_at | 2026-05-24 15:35:46 | (未显示) | — |
| lot01 remaining_quantity | 0 | **1848** ⚠️ | ❌ |
| lot02 release_batch_id | `17860336...4f41e` | 同 | ✅ |
| lot02 batch_sequence | lot02 | lot02 | ✅ |
| lot02 cargo_name | 铁矿 | 铁矿 | ✅ |
| lot02 batch_quantity | 3000 | 3000 | ✅ |
| lot02 dispatch_status | in_progress | 发运中 | ✅ |
| lot02 actual_wagon_count | 0 | 0 | ✅ |
| lot02 contract_no | (空) | (空) | ✅ |

**蓝鳍 wagons**:

| lot | DB wagon_count | DB delivered | DB confirmed | Dashboard formal_wagon |
|---|---|---|---|---|
| lot01 | 46 | 46 | 46 | **18** ❌ |
| lot02 | 0 | 0 | 0 | 0 ✅ |

### 3.2 长航滨海

| 指标 | DB 实际值 | Dashboard 显示值 | 一致？ |
|---|---|---|---|
| release_batch_id | `7e8463...086ff` | 同 | ✅ |
| ship_name | 长航滨海 | 长航滨海 | ✅ |
| batch_sequence | (空) | (空) | ✅ |
| cargo_name | 红土镍矿 | 红土镍矿 | ✅ |
| batch_quantity | 3000 | 3000 | ✅ |
| contract_no | JGCG-SFY-HTNK20260501 | JGCG-SFY-HTNK20260501 | ✅ |
| dispatch_status | completed | 已发完 | ✅ |
| actual_wagon_count | **46** | **0** ⚠️ | ❌ |
| confirmed_received_at | 2026-05-22 01:44:06 | (未显示) | — |
| remaining_quantity | 0 | **3000** ⚠️ | ❌ |
| project | jilin_jingang_jinzhou | jilin_jingang_jinzhou | ✅ |
| wagon_count (DB) | 46 | 0 | ❌ |
| delivered_count | 46 | 0 | ❌ |
| confirmed_count | 46 | 0 | ❌ |

---

## 4. 问题分析

### 问题 1: formal_wagon_count 数据源错误

**根因**: `_fetch_formal_summary_read_only()` 从 95306 DB 的 `shipment_release_batch_matches` 查数据。该表仅在 `inspection_95306_reconciler` 写入。

蓝鳍 lot01 的 18 车（1-18位）是通过 inspection reconciler 匹配的 → 95306 DB 有 18 条 match → dashboard 显示 18。lot02 的 28 车（26-60位）是通过 `departure_text → query_95306 → create_wagon_shipments` 创建的 → 没有 inspection reconciler 写入 → 95306 DB 无 match → dashboard 不显示。

长航滨海 46 车全部通过 departure_text 创建 → 95306 DB 无 match → dashboard 显示 0。

**影响**: 看板上的"已正式入库车数"严重偏低：
- 蓝鳍 lot01: 46 → 只显示 18
- 长航滨海: 46 → 只显示 0

### 问题 2: cargo_name 显示 cargo_product_name

蓝鳍 lot01 的 DB: `cargo_name=铁矿, cargo_product_name=印粉`。看板显示 `印粉` 而非 `铁矿`。

根因: `generate_dispatch_board_data()` 中 cargo_name 优先取 `cargo_product_name`。

### 问题 3: remaining_quantity 计算偏差

蓝鳍 lot01 显示 `remaining_quantity=1848`（实际应为 0），长航滨海显示 `remaining_quantity=3000`（实际应为 0）。

根因: `remaining_quantity` 使用了 `planned_quantity - formal_weight` 的计算逻辑，其中 `formal_weight` 来自 95306 DB 的匹配数据（仅 18 车），而非 wagon_shipments 的实际吨数。

### 问题 4: lot vs 发运列次 混淆检查

**未混淆。** Dashboard 正确区分：
- `batch_sequence` = lot（放货批次）→ 列标题 "lot"
- `formal_wagon_count` = 已正式入库车数 → 列标题 "已正式入库车数"
- `formal_shipments` = 已发运车辆明细 → 列标题 "已发运车辆明细"

但是 `formal_shipments` 仅来自 95306 matches，不包含 departure_text 创建的车。这导致"已发运车辆明细"列也只显示 18 车而非 46 车。

### 问题 5: 长航滨海 project 别名

DB: `project=jilin_jingang_jinzhou`（英文别名），而 蓝鳍 lot01 显示 `project=吉林金钢-锦州港铁矿发运项目`（中文名称）。这是历史数据不一致，不影响功能但影响看板可读性。

---

## 5. 一致性校验总结

| 校验项 | 结果 |
|---|---|
| release_batches 数量一致 | ✅ 17 = 17 |
| wagon count 一致 | ❌ 蓝鳍 18≠46, 长航滨海 0≠46 |
| delivered_count 一致 | ❌ （看板不直接显示） |
| confirmed_received_count 一致 | ❌ （看板不直接显示） |
| 区分 lot / 发运列次 | ✅ 未混淆 |
| 全局 formal_wagon_count | 654 (多项目汇总) |
| 全局 formal_weight | 44124.0 |

---

## 6. 回答

| # | 问题 | 答案 |
|---|---|---|
| 1 | 普通货运看板现在能否打开？ | ✅ 可以，`http://127.0.0.1:8765/dispatch_board.html` |
| 2 | 看板数据是否来自最新 sop_agent.db？ | ✅ 是（刚刷新） |
| 3 | 蓝鳍 lot1 / lot2 是否显示正确？ | ❌ lot1 wagon=18（应=46），lot2 状态正确但 wagon=0 |
| 4 | 长航滨海是否显示正确？ | ❌ wagon=0（应=46），status=已发完（正确） |
| 5 | 看板是否混淆 lot 与发运列次？ | ✅ 未混淆 |
| 6 | 是否需要前端/数据 payload 修复？ | ⚠️ 是 — formal_wagon_count 需从 sop_agent.db wagon_shipments 读取 |
| 7 | 下一步建议 | 修复 `_fetch_formal_summary_read_only`，增加 sop_agent.db wagon_shipments 聚合逻辑 |

---

## 7. 修复方案（建议，不执行）

`_fetch_formal_summary_read_only()` 当前仅读 95306 DB。应改为两层聚合：

1. **先读 sop_agent.db wagon_shipments** — GROUP BY batch_id，COUNT + SUM
2. **再读 95306 DB shipment_release_batch_matches** — 仅用于补充 car_details（formal_shipments）
3. **合并** — formal_wagon_count 优先取 sop_agent.db 的数据

这样可以同时覆盖 inspection reconciler 和 departure_text 两种路径。

---

## 8. 测试结果

```
216 passed in 3.41s
```

---

## 9. 文件

- `reports/ordinary_freight_dashboard_acceptance_r47.md` — 本报告
- `dashboard/dispatch_board_data.json` — 刷新后的看板数据（未 commit，运行时文件）
- `dashboard/dispatch_board.html` — 看板 HTML 模板（未修改）
