# 九三大豆循环车组 Tracking Requirement 校验报告 — 2026-05-22

## Scope
- 项目：九三大豆铁路发运 / 九三铁岭大豆循环运输 / 玛格丽特
- 要求：
  - Z 向重车必须自动通过 95306 更新状态
  - K 向返空可以人工报送，不要假装 95306 能自动识别返空
  - 检查是否仍存在“重车已到但看板显示待同步”的问题
- 约束：只做校验，不修代码、不改数据

## 读取到的最新素材
- `ops-data-hub/data/jiusan_cycle.db`
- `dashboard/jiusan_dashboard_data.json`
- `rail95306-sync/runtime/95306_collection.sqlite3`
- `rail95306-sync` 最新 `query_runs`：`id=17029`，`queryCargoSend`，`status=completed`，`http_status=200`

## 已确认的差异
- **95306 自动触发本身是成功的**：`query_runs.id=17029` 显示请求已完成，`status=completed`，`http_status=200`，`return_code=00200`，`merged_result_count=728`。
- **但看板/本地主记录没有把最新 Z 向重车闭环同步进去**：
  - `dashboard/jiusan_dashboard_data.json` 中 `container_cycle_train_03`、`container_cycle_train_04` 仍显示 `status_text: 待同步`
  - 两条卡片的 `tracking.total_cars = 0`
  - `dashboard` 的 `95306_status.last_scan` 仍停留在 `2026-05-21T11:02:23.225494`，不是本次最新触发时间
- **`jiusan_cycle.db` 中没有 train03 / train04 的单车 tracking 明细**：
  - `jiusan_tracking_status` 里找不到 `container_cycle_train_03`
  - `jiusan_tracking_status` 里找不到 `container_cycle_train_04`
  - 这意味着它们现在仍然停留在“卡片级描述”，没有进入可自动更新的单车级跟踪表

## K 向返空的差异
- **`列1K1` 仍然只是在 `container_cycle_train_01.return_time` 中体现**，属于手工返空口径，没有单独的 95306 返空识别结果。
- **`列2K1` 当前没有 95306 返空记录**：在 `rail95306-sync/runtime/95306_collection.sqlite3` 里没有查到 `origin_name = 新台子、destination_name = 高桥镇` 的大豆返空记录。
- 因此，`列2K1` 现在仍然不能靠 95306 自动识别，只能等人工报送落账。

## “重车已到但显示待同步”问题
- **仍存在**。
- 具体表现：
  - 95306 数据层已经有 5/21 16:15、5/21 20:47 这两趟新台子相关重车数据
  - 但看板上对应的 train 卡片仍是 `待同步`
  - 说明“95306 自动触发 → 看板状态刷新”这条链路目前没有贯通到展示层

## 车组状态对照
- `container_cycle_train_01`
  - Z 向：已自动同步到 95306
  - K 向：返空口径存在，但属于人工回港记录
- `container_cycle_train_02`
  - Z 向：已自动同步到 95306
  - K 向：当前缺失
- `container_cycle_train_03`
  - Z 向：95306 已有实绩，但看板仍 `待同步`
- `container_cycle_train_04`
  - Z 向：95306 已有实绩，但看板仍 `待同步`

## 证据路径
- `/Users/qicai21/projects/repos/ops-data-hub/dashboard/jiusan_dashboard_data.json`
- `/Users/qicai21/projects/repos/ops-data-hub/data/jiusan_cycle.db`
- `/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3`

## 结论
- 95306 自动触发：通过。
- Z 向自动更新链路：未完全打通到看板 / 单车跟踪表。
- K 向返空：仍应按人工报送口径处理，当前 `列2K1` 还没有自动识别结果。
- “重车已到但显示待同步”：仍然存在。
- 本次未做任何修正。
