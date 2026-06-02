# Dispatch Board JSON Schema

> 权威看板位于 `ops-data-hub/dashboard/`，本文档描述 `dispatch_board_data.json` 的数据结构。

## 顶层结构

```json
{
  "meta": {},
  "summary": {},
  "pending_items": [],
  "release_batches": [],
  "inspection_candidates": [],
  "reconcile_plans": []
}
```

## 1. meta

| 字段 | 类型 | 说明 |
|---|---|---|
| `generated_at` | string | 生成时间 (ISO 8601 / `YYYY-MM-DD HH:MM:SS`) |
| `source_db` | string | 业务库绝对路径 (`sop-data-hub/data/sop_agent.db`) |
| `rail95306_db` | string | 95306 只读库绝对路径 |
| `generator_version` | string | 生成器版本标识 |
| `refresh_reason` | string | 本次刷新原因 (如 `manual_refresh`、`release_batch_updated`) |

## 2. summary

| 字段 | 类型 | 说明 |
|---|---|---|
| `pending_total` | int | 待落实事项总数 (各 pending 分类汇总) |
| `pending_text_release` | int | 待匹配文字放货消息数 |
| `pending_release_plan_images` | int | 待匹配出港计划/放货图片数 |
| `pending_inspection_assignment` | int | 待指认检装车通知单数 |
| `pending_95306_check` | int | 待95306校验候选数 |
| `active_release_batches` | int | 当前发运中批次数 |
| `release_batch_total` | int | release_batch 总数 |
| `matched_inspection_candidates` | int | 已匹配检装车候选数 (含已正式入库) |
| `manual_candidate_count` | int | 待人工匹配候选数 |
| `unassigned_candidate_count` | int | 未分配待人工候选数 |
| `formal_wagon_count` | int | 已正式入库车数 |
| `formal_weight` | float | 已正式入库重量 |

## 3. pending_items

待处理事项列表，优先级从高到低。

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | 内部类型标识 |
| `display_type` | string | 展示用类型名 |
| `ship_name` | string | 船名 |
| `cargo_name` | string | 货名 |
| `quantity` | string | 数量/车数 |
| `wagon_count` | int | 车数 (检装车相关) |
| `plan_no` | string | 计划号 |
| `contract_no` | string | 合同号 |
| `status` | string | 状态 |
| `reason` | string | 挂起/未处理原因 |
| `candidate_lots` | string | 候选 lot 列表 (逗号分隔) |
| `source_image_path` | string | 来源图片绝对路径 |
| `source_json_path` | string | 来源 JSON 绝对路径 |
| `recorded_at` | string | 记录时间 |

## 4. release_batches

| 字段 | 类型 | 说明 |
|---|---|---|
| `release_batch_id` | string | release_batch 记录 ID |
| `project` | string | 项目名称 |
| `ship_name` | string | 船名 |
| `destination_station` | string | 到站 |
| `batch_sequence` | string | lot / 批次序号 |
| `planned_quantity` | float | 计划吨数 |
| `batch_date` | string | 批次日期 |
| `cargo_name` | string | 货物品名 |
| `plan_no` | string | 计划号/订单号 |
| `contract_no` | string | 合同号 |
| `status` | string | 当前状态 (发运中/已发完/暂停/不发运/待人工确认) |
| `matched_candidate_count` | int | 已匹配候选数 |
| `manual_candidate_count` | int | 待人工候选数 |
| `candidate_lots` | string | 候选 lot 列表 (逗号分隔) |
| `formal_wagon_count` | int | 已正式入库车数 |
| `formal_weight` | float | 已正式入库重量 |
| `remaining_quantity` | float | 理论剩余货量 |
| `source_image_path` | string | 原始图片绝对路径 |
| `source_json_path` | string | JSON 绝对路径 |
| `state_file_path` | string | 状态文件绝对路径 |
| `formal_shipments` | array | 正式发运车辆明细 |

### 4.1 formal_shipments 元素

| 字段 | 类型 | 说明 |
|---|---|---|
| `car_no` | string | 车号 |
| `time` | string | 装车/事件时间 |
| `inspection_file` | string | 检装车通知单图片路径 |

## 5. inspection_candidates

| 字段 | 类型 | 说明 |
|---|---|---|
| `candidate_id` | string | 候选记录 ID |
| `project` | string | 项目名称 |
| `ship_name` | string | 船名 |
| `destination_station` | string | 到站 |
| `candidate_lot` | string | 候选 lot |
| `release_batch_id` | string | 关联的 release_batch ID |
| `source_image_path` | string | 来源图片路径 |
| `source_json_path` | string | 来源 JSON 路径 |
| `parsed_car_count` | int | 解析车数 |
| `defect_car_count` | int | 瑕疵车数 |
| `status` | string | 状态 |
| `needs_review` | bool | 是否需要人工复核 |
| `review_reasons` | string | 复核原因 |
| `created_at` | string | 创建时间 |

## 6. reconcile_plans

| 字段 | 类型 | 说明 |
|---|---|---|
| `plan_id` | string | 比对计划 ID |
| `candidate_id` | string | 关联候选 ID |
| `release_batch_id` | string | 关联 release_batch ID |
| `safe_to_commit` | bool | 是否可安全提交 |
| `planned_rows` | int | 计划行数 |
| `excluded_rows` | int | 排除行数 |
| `review_reasons` | string | 复核原因说明 |
| `status` | string | 状态 |
| `created_at` | string | 创建时间 |
| `committed_at` | string | 提交时间 |
