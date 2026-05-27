# R28: 吉林金钢 SOP 制单后链路核查

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Scope:** `config/project_sops/jilin_jingang.yaml` — 只读审计，不改代码

---

## 一、SOP 原始节点树

以下逐节点列出，源文件 `config/project_sops/jilin_jingang.yaml`。

### 节点表

| # | SOP原文节点名 | 状态 | 输入 | 输出 | 下一节点 |
|---|-------------|------|------|------|---------|
| 1 | `create_release_batch` | 触发 | 出港计划通知单 image | `release_batch` 记录 | → create_pending_departure_candidate |
| 2 | `create_pending_departure_candidate` | 触发 | 手写箱号车号表 image / 文字发车报告 | `pending_departure_candidate` 记录 | → WAIT_95306_CONFIRM |
| 3 | **WAIT_95306_CONFIRM** | **等待** | 95306 发车确认 | — | **SOP 终点。无下一节点。** |

### 来源行

```
L13: 装车后进入 pending_departure_candidate (WAIT_95306_CONFIRM)，不得直接入库
L40-42:
  departure_flow:
    pre_95306_status: "WAIT_95306_CONFIRM"
    pending_node: "pending_departure_candidate"
```

---

## 二、WAIT_95306_CONFIRM 之后检查

### 是否存在 WAIT_DELIVERED？

**不存在。** 全文搜索 `WAIT_DELIVERED`、`DELIVERED`、`delivered`、`确认收货` — 0 hits。

### 是否存在 CONFIRMED_RECEIVED？

**不存在。** 全文搜索 `CONFIRMED_RECEIVED`、`confirmed`、`配送完成` — 0 hits。

### 是否存在以下节点？

| 节点 | SOP是否存在 | 证据 |
|------|:---:|------|
| 发车 → 95306跟踪 | **否** | `departure_flow` 只定义 `pre_95306_status`，无跟踪触发 |
| 95306确认 → 到站 | **否** | 到站名 `四平` 仅在 `project_meta.destination_station` 出现，无对应节点 |
| 到站 → 交付 | **否** | 无 `交付`、`delivery`、`handover` 关键词 |
| 交付 → 确认收货 | **否** | 无 `确认收货`、`CONFIRMED_RECEIVED`、`receive` 关键词 |

---

## 三、SOP 结束点

SOP 最后一个显式定义的状态：

```
departure_flow.pre_95306_status: "WAIT_95306_CONFIRM"
```

之后 SOP 不再定义任何节点、状态转移、或输出。

`factory_upload` 段 (`L138-151`) 声明 `enabled: false`，明确标注"预留，不自动执行"。

`report_artifact` 段 (`L118-136`) 定义 Excel 格式，但 **未绑定到任何 routing node** — 无法自动触发报表生成。

---

## 四、为什么 长航滨海 = in_progress 而非 confirmed_received

### SOP 原因

| 项目 | SOP事实 |
|------|---------|
| 初始状态 | `create_release_batch` 创建 `release_batch` 时，`dispatch_status` 默认值为 `in_progress` |
| 状态转移 | SOP 不定义任何 `dispatch_status` → `confirmed_received` 的转移 |
| 可能的状态 | SOP 仅定义 `WAIT_95306_CONFIRM` 为装车后状态，**不是** `dispatch_status` 值 |
| 结束机制 | SOP 不定义"结束"对应的 `dispatch_status` 值 |

### 逐字证据

SOP 全文（161 行）中：

- `dispatch_status` — **0 hits**（SOP 完全未定义 `dispatch_status` 字段的任何值）
- `confirmed_received` — **0 hits**
- `completed` — 仅在 `report_targets` 上下文中（不是状态）
- `in_progress` — **0 hits**
- 状态变更指令 — 仅在 `departure_flow` 中定义 `pre_95306_status`，此为 departure 维度的标记，非 release_batch 维度的 dispatch_status

### 结论

长航滨海 = `in_progress` 是因为 **SOP 未定义任何状态转移机制将 `dispatch_status` 从 `in_progress` 推进到任何终态**。`confirmed_received` 在 SOP 中不存在。

---

## 五、完整 SOP 节点链（可视化）

```
WeChat消息
  │
  ├─[出港计划通知单 image]──→ create_release_batch ──→ release_batch (dispatch_status=in_progress)
  │
  └─[手写箱号车号表 image / 文字发车报告]──→ create_pending_departure_candidate
                                                    │
                                                    ▼
                                           WAIT_95306_CONFIRM
                                                    │
                                                    ▼
                                           ┌─ SOP 终点 ─┐
                                           │ 无后续节点   │
                                           │ 无状态转移   │
                                           │ 无交付确认   │
                                           └────────────┘
```

---

**零代码变更。纯 SOP 审计。**
