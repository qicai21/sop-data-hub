# 九三大豆 Phase 2.5-B 追踪链路修复报告

## 范围
- 项目：九三大豆铁路发运 / 玛格丽特
- 分支：`ops-data-hub/main`
- 约束：不进入 Phase 3；不改数据库结构；不做返空自动识别；不加定时任务

## 问题结论
问题不是 95306 查询失败，而是链路断在：

`95306 结果 → tracking_status → dashboard`

表现为：
- `train03 / train04` 在 95306 中已有数据
- 但 `jiusan_tracking_status` 没有对应记录
- dashboard 仍显示 `待同步`

## 本次修复
### P0：打通主链路
修改 `scripts/jiusan_tracking_update.py`：
- 改为按最小身份映射读取 95306 shipments
- 用 `departed_at / arrived_at` 将四列拆分为：
  - `container_cycle_train_01`
  - `container_cycle_train_02`
  - `container_cycle_train_03`
  - `container_cycle_train_04`
- 将结果写入 `jiusan_tracking_status`
- 再由 `scripts/jiusan_board_generate.py` 读取该表更新 dashboard

### P1：新增最小身份映射 JSON
新增：`samples/jiusan_cycle_train_identity_map.json`

映射示例：
- `container_cycle_train_01` → `列1Z1`
- `container_cycle_train_02` → `列2Z1`
- `container_cycle_train_03` → `列3Z1`
- `container_cycle_train_04` → `列1Z2?`（保留问号，标记为 tentative）

说明：先用 JSON 配置，不改数据库结构。

### P2：未做项
- 没有做返空自动识别
- 没有进入 Phase 3
- 没有加定时任务

返空仍按人工报送口径处理。

## 验证结果
### 1）`jiusan_tracking_status` 已写入四列
- `container_cycle_train_01`：54
- `container_cycle_train_02`：54
- `container_cycle_train_03`：52
- `container_cycle_train_04`：52

### 2）dashboard 已不再显示 `待同步`
当前生成结果中：
- `container_cycle_train_03`：`已到达`，`total_cars=52`
- `container_cycle_train_04`：`已交付`，`total_cars=52`

### 3）refresh 仍是手动入口
仍通过 `python3 scripts/jiusan_refresh_board.py` 手动刷新；本次没有引入自动定时任务。

## 交付文件
- `reports/2026-05-22_phase2_5b_tracking_pipeline_report.md`
- `samples/jiusan_cycle_train_identity_map.json`
- `scripts/jiusan_tracking_update.py`

## 结论
本次已把 `95306 → tracking_status → dashboard` 链路打通，`train03 / train04` 不再停留在 `待同步`。