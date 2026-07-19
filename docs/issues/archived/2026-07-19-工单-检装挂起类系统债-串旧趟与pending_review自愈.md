# 工单：检装/挂起类系统债 — car_no 串旧趟兜底 + pending_review 自愈重试

- **日期**：2026-07-19
- **状态**：已完成
- **范围**：事件级取数 car_no 回退路径；`pending_match_verifier` 挂起自愈
- **theme**: T4_挂起未触发/短路跳过 · T6_串票错归属/旧趟 · 检装

## 背景

审计 P0/P1 中与「检装成功路径」直接相关的两处系统债：

1. **串旧趟（事件级取数）**  
   2026-07-08 工单已用 **ydid 优先** 修好主路径；但 `upload_wagons.fetch_wagons` / `departure_excel._extract_rows` 在 **仅有 car_nos、无 ydids** 时仍按 `batch_id + car_no` 全捞，循环车号会把旧趟一并带出（马兰幸福 22 车 → 32 行事故的同根因回退路径）。

2. **挂起静默沉底**  
   verifier 只扫 `pending_95306_match` / `pending_freight_info`。  
   配置优先级后、open lot 变化后，本可自愈的 `pending_review`（`multiple_candidates` / `no_open_batch` / `multi_candidate_tied:*` 等）不会被自动重试，只能人工清或等新图。

## 修复

### A. car_no 无 ydid 时每车只取最新 ticketed_at

| 入口 | 变更 |
| --- | --- |
| `upload_wagons.fetch_wagons` | car_nos 分支加 `ticketed_at = MAX(...)` 子查询 |
| `departure_excel._extract_rows` | 同上 |

优先路径仍是 **ydids**（不变）。本补丁只收紧 car_no 回退，避免再串旧趟。

### B. pending_review 可自愈 reason 纳入 verifier

`pending_match_verifier.verify_pending_candidates` 额外扫描：

- `candidate_status = pending_review`
- 且 reason ∈ `multiple_candidates` / `no_open_batch` / `no_match` / `bad_input`  
  或 `reason` 前缀 `multi_candidate_tied`

**不重试** 需改载荷的 reason（如 OCR 页脚歧义等），避免空转刷链。

超时逻辑未变：仅 `pending_95306_match` + stage `waiting_95306_tickets` 才 `timeout_manual_review`。

## 测试

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_event_scoped_wagon_filters.py \
  tests/functional/test_pending_match_verifier_retry_before_timeout.py
```

- car_no-only：每车 1 行，时间戳为新趟  
- mixed pending：可自愈 3 条 + 95306/freight 被扫；OCR 类 pending_review 跳过

## 关联

- 前序：`2026-07-08-工单-朝钢马兰幸福22车事件级取数串旧趟导致excel32行与鞍钢上传错口径.md`（ydid 主路径）
- 前序：`2026-07-18/19` 多 lot priority / infer 同分（修复后仍需 verifier 回扫）
- 审计：`docs/2026-07-19-评审-sop-data-hub工单与自动化审计.md` §9 P0.4 失败可见/可重试

## 收尾

- 代码已合 main（本 commit）
- 需重启/触发 **text-watch** 与 **pending_match_verifier** 调度，加载新代码并扫存量可自愈挂起
