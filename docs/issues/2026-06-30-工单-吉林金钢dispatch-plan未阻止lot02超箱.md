# 工单:吉林金钢 dispatch_plan 未阻止 lot02 超箱

- **类型**:分票计划/箱级归属缺陷
- **发现日期**:2026-06-30
- **发现来源**:用户从看板发现马兰希望 lot02 显示 366 箱,与用户指定 lot02 306 箱限制不符
- **状态**:处理中（已完成数据纠偏和代码修复,待回归验证/提交）
- **严重度**:高 —— 用户指定的按放货批次装箱数量限制未生效,导致箱级归属继续写入已满 lot,后续发运 Excel/上传/对账口径都会错 lot

## 背景

吉林金钢集装箱业务有按 release_batch 限制装箱发运数量的机制,落点是 `release_batch_dispatch_plan`:

- `planned_box_count`:该 lot 计划承接箱数
- `allocated_box_count`:已分配箱数
- `priority_order`:分配优先级
- `status`:active/completed

用户指定马兰希望:

- lot02 先填满 306 箱
- 再填 lot03 306 箱

当前计划表:

- lot02 `planned_box_count=306`, `allocated_box_count=216`, `status=active`
- lot03 `planned_box_count=306`, `allocated_box_count=0`, `status=active`

但箱级事实表 `wagon_container_shipments` 当前:

- lot02: 366 箱
- lot03: 0 箱

## 现象

马兰希望 lot02 箱级记录按制票日分布:

- 2026-06-24: 70 箱
- 2026-06-27: 80 箱
- 2026-06-28: 108 箱
- 2026-06-30: 108 箱

合计 366 箱,超过 lot02 限额 306 箱 60 箱。后续箱没有切入 lot03。

## 根因

`release_batch_dispatch_plan` 是在 lot02 已经有历史箱级入库后创建的。

创建计划时,`set_plan()` 对新计划的 `allocated_box_count` 从 0 起算,没有按 `wagon_container_shipments` 中已有箱数初始化。

马兰希望 lot02 在计划创建前已有 150 箱:

- 2026-06-24:70 箱
- 2026-06-27:80 箱

因此后续 2026-06-28、2026-06-30 两次 108 箱虽然触发了 plan 累加,但系统只认为 lot02 已分配 216 箱,实际箱表已经达到 366 箱。

脱节表现:

- `wagon_container_shipments.batch_id` 继续落在 lot02。
- `release_batch_dispatch_plan.allocated_box_count` 仍停在 216,没有反映实际 366。
- lot02 没有在达到 306 时转 `completed`,lot03 没有承接超出的 60 箱。

## 数据修正

已按用户指定计划恢复一致状态。备份:

- `data/sop_agent.db.bak-jljg-malan-dispatch-plan-20260630-132614`

修正规则:

- lot02 保留前 306 箱。
- 2026-06-30 这批 108 箱中,前 48 箱补满 lot02,后 60 箱移动到 lot03。
- `release_batch_dispatch_plan`:
  - lot02 `allocated_box_count=306`, `status=completed`
  - lot03 `allocated_box_count=60`, `status=active`
- 同步重算 lot02/lot03 发运量、剩余量、车/箱统计。

修正后:

- lot02:306 箱,153 车,`dispatch_status=all_loaded`
- lot03:60 箱,30 车,`dispatch_status=loading`

## 系统修复

- `src/sop_hub/sop/dispatch_plan.py`
  - `set_plan()` 写入计划时读取 `wagon_container_shipments` 中该 release batch 已有箱数。
  - `allocated_box_count` 使用 `max(既有计数, 箱表实际箱数)`。
  - 如果实际已达到 `planned_box_count`,计划状态直接置为 `completed`。
- `tests/test_dispatch_plan_set_plan.py`
  - 增加“计划创建前已有箱”的回归测试。
  - 增加“已有箱数达到计划数时直接 completed”的回归测试。

## 验收标准

- 马兰希望 lot02 箱表实际箱数为 306。✅
- 马兰希望 lot03 箱表实际箱数为 60。✅
- dispatch_plan 计数与箱表一致。✅
- 后续再发一列马兰希望时,lot03 从 60 继续累加,lot02 不再增加。
- 增加回归测试覆盖“晚创建 plan 时已有箱必须计入 allocated_box_count”。✅
