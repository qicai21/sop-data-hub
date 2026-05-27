# R24: Real Ordinary Freight Workflow Acceptance Audit

**Date:** 2026-05-27  
**Branch:** `codex/sop-real-sop-topology-audit-20260525`  
**Scope:** 吉林金钢 & 朝阳钢铁 两条真实货运链路端到端验收

---

## Part A: 长航滨海 — SOP 缺口审计

### 问题

```
用户现实 = 已确认收货
系统状态 = in_progress
```

### A.1 — SOP 是否明确 收到发车 → 持续跟踪 → 95306 → 确认收货？

**否。SOP 未覆盖 95306 后的确认收货链路。**

SOP `jilin_jingang_jinzhou_baseline.yaml` 定义了：
- `departure_flow.pre_95306_status: "WAIT_95306_CONFIRM"` — 装车后进入 WAIT 状态
- `departure_flow.pending_node: "pending_departure_candidate"` — 挂起节点

但 SOP **没有定义**：
- WAIT_95306_CONFIRM 之后的 transition（如何从 WAIT 变为 confirmed）
- 95306 验证通过后的 `target_node`
- 确认收货对应的 `dispatch_status` 变更（dispatched? completed?）
- 谁负责触发确认收货（自动 vs 人工指令）

### A.2 — 当前链路能否自动走通 微信群 → 发车 → 95306 → 确认收货？

**不能。卡在 3 层：**

| 层级 | 卡点 | 证据 |
|------|------|------|
| **DB** | `release_dispatch_match_rules` 中无长航滨海条目 | 查询返回空（仅有蓝鳍） |
| **Dashboard** | `inspection_ingestion_candidates` 仅一条 `factory_upload_completed` — 手工补录，非 SOP 自动 | `pdc_jljg_20260521_152211` |
| **95306** | `rail95306-sync` 的 `shipment_release_batch_matches` 只读挂载到 dispatch_board，无写回路径 | `dispatch_board.py:_fetch_formal_summary_read_only` |

**完整卡点链路：**

```
SOP 定义了:
  微信发车消息 → create_pending_departure_candidate → WAIT_95306_CONFIRM

但实际代码:
  1. source_watcher → events (✓)
  2. monitoring_plan_matcher → match (✓)
  3. dashboard_intent → "ready" (✓，仅标记 ready，不执行)
  4. dispatch_board → HTML only (只读视图，无写回)
  5. release_dispatch_match_rules → 长航滨海 缺失 (✗)
  6. 95306 关联 → 只读统计 (✗ 无自动写回)
```

### 根因

长航滨海使用 project_id `jilin_jingang_jinzhou`（不含"项目"后缀），但：
- `ORDINARY_FREIGHT_PROJECTS` 在 `dashboard_intent.py` 中只接受 `"jilin_jingang_jinzhou"` → 匹配成功但没有后续动作
- `release_dispatch_match_rules` 自动创建逻辑只针对 出港计划通知单 触发（`create_release_batch` node），对发车通知单 **不创建 match rule**
- 95306 `shipments` 表的运单与 release_batches 的关联目前仅通过 `dispatch_board.py` 做只读统计渲染

---

## Part B: 蓝鳍 发运通知 — 7 节点状态

蓝鳍当前状态：

| 节点 | 名称 | 状态 | 内容 |
|------|------|------|------|
| 1 | chat_records | **用户待补发** | 蓝鳍发运通知尚未发送到微信群 |
| 2 | runtime/events | 等待 | 消息到达后由 live_service 生成 |
| 3 | dashboard_intents | 等待 | 消息匹配后由 pipeline 生成 |
| 4 | dashboard_state | 等待 | 消息匹配后更新 |
| 5 | sop_agent.db | **部分就绪** | release_batch 已创建（3000吨/18车），dispatch_status=dispatched |
| 6 | 发车Excel | **不可用** | `gen_jljg_excel.py` 硬编码 长航滨海，需改 |
| 7 | 收货人JSON | **端点可达** | `http://111.26.178.96:88` → 200 OK |

### 节点 5 详情（release_batches / wagon_shipments）

```
蓝鳍: 18 wagons, 全部 已发车
  ticketed_at: 2026-05-23
  合同: HNMC20260520-1X-1
  印粉 3000 吨
  四平
  dispatch_status: dispatched (用户手动标记)
```

### 节点 6 问题（gen_jljg_excel.py）

```python
# 硬编码问题：
ws.cell(row=i+1, column=10, value='长航滨海')  # → 应为动态船名
ws.cell(row=i+1, column=2, value='JGCG-SFY-HTNK20260501')  # → 应为动态合同号
OUT_DIR = '/Users/qicai21/projects/repos/ops-data-hub/reports'  # → 应改为 sop-data-hub
payload['first'] = '芦家屯装车发运明细'  # → SOP 明确禁止芦家屯
```

### 节点 7 验证

```
curl http://111.26.178.96:88 → HTTP 200
```
四平工厂系统在线，上传端点 `POST /prod-api/sales/transportOrder/insert` 可达（dry-run 确认，无实际 POST）。

---

## Part C: 发车 Excel & 四平上传

### 发车 Excel

- `gen_jljg_excel.py` 存在但硬编码 长航滨海
- 生成逻辑可工作（openpyxl + header_style）
- 箱号列为占位符 `待补-需从95306API提取箱号`
- 输出目录未迁移（ops-data-hub → sop-data-hub）

### 四平上传脚本

**不存在独立的上传脚本。** 上传逻辑仅在：
- SOP YAML `factory_upload` 段（声明端点/凭证，`enabled: false`）
- `gen_jljg_excel.py` 的 dry-run 预览（只打印 JSON，不 POST）

```
端点: http://111.26.178.96:88/prod-api
登录: POST /auth/login {username:"saibin", password:"Xts@95306"}
上传: POST /sales/transportOrder/insert (一车一发)
SOP 明确: enabled: false — 暂不自动上传
```

---

## Part D: 木森17 — 批次模型判定

### 数据库现状

```
SELECT * FROM release_batches WHERE ship_name='木森17'

1 row:
  batch_sequence: lot01
  batch_quantity: 15975.0
  47 wagons  ← 全部绑定到同一个 batch_id
```

### 判定

**当前数据模型：一船一批。**

证据：
- `release_batches` 中木森17仅一条记录
- 47车全部绑定到同一个 `batch_id`
- `dispatch_board.py` 的渲染逻辑按 `project ASC, dispatch_status CASE` 排序，无批次聚合概念

### 15975 属于：单批

SOP `chaoyang_steel_baseline.yaml` 中：
```
没有 batch_sequence 概念
没有 total_planned_quantity / remaining_quantity 逻辑
```

SOP 声明"不包含文字放货链路"，但对木森17的现实数据（10600+5375 两段放货）**未记录**。

### SOP 缺失

| 缺失项 | 影响 |
|--------|------|
| 批次拆分机制（lot01/lot02） | 放货通知分两次发但 DB 只存一个 batch |
| 多段放货叠加逻辑 | 10600+5375 如何在 single batch 中表达不清 |
| batch_sequence 在朝阳项目中的语义 | SOP 存在 lot01 但未定义行为 |
| 补放货流程 | 木森17 5375 吨补放货无对应 SOP 节点 |

---

## 汇总

| 项目 | 船 | 系统状态 | 现实状态 | 能否自动走通 | 卡点 |
|------|-----|----------|----------|-------------|------|
| 吉林金钢 | 长航滨海 | in_progress | 已确认收货 | **否** | 缺 match rule、95306 确认收货链路 |
| 吉林金钢 | 蓝鳍 | dispatched | 待补发运通知 | 等消息 | Excel 脚本硬编码 |
| 朝阳钢铁 | 木森17 | in_progress | 10600+5375 两段 | **否** | 一船一批模型不匹配两段放货 |

---

**零代码变更。纯审计。**
