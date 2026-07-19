# 工单：all_loaded「停车场」与交付确认推进

- **提出日期**：2026-07-19
- **状态**：处理中
- **来源**：用户转述 Claude 评审结论 + 本轮库内只读盘点
- **范围**：lifecycle closeout / 95306 状态语义；装车道线完备性；**不**重发 Excel、**不**重传门户

## 现象 / 背景

- Claude 结论：`all_loaded` 成了大型停车场，但业务终点不是「发完」而是 **确认交付**。
- 可接受的 95306 运单状态（用户）：
  - `货物已交付`
  - `确认收货`
  - `已卸车`（少见）
- 同时要求必要字段齐全（至少 **装车道线 `loading_line`**）。

## 只读盘点（2026-07-19）

| 项目 | all_loaded 批次数 | 备注 |
| --- | ---: | --- |
| 吉林金钢 | 5 | 箱级 `latest_stage_key` 大量为空；蓝鳍 lot7/8 缺 loading_line |
| 中唐 | 5 | 车级多为已制单/已发车，仅部分交付 |
| 朝钢 | 0（多 closed/confirmed_received） | shipped_is_completed 模式 |

现 closeout 逻辑：

- 扫描 `loading|all_loaded|tracking|delivered`
- 全车 `latest_stage_key ∈ {delivered, unloading_completed}` 才推 `confirmed_received`
- **未**认 `status_name` 文本；**未**映射 `确认收货` / `已卸车`
- **未**检查 loading_line
- 集装箱静默 2 天闸仍保留

## 根因判断

1. 语义不全：交付判断只靠 stage_key，status_name 与用户口径未对齐。
2. 吉林箱级状态同步薄弱：`shipment_status_sync` 主路径偏 `wagon_shipments`，箱表 stage 空则永远 all_received=false。
3. 字段闸缺失：缺装车道线仍可能在 stage 齐时被推确认（或不该推时混乱）。

## 本轮目标（可测、可提交）

1. closeout「已收货」判定扩展：
   - stage：`delivered` / `unloading_completed`
   - 或 status_name ∈ {`货物已交付`,`确认收货`,`已卸车`}（及已有「已交付」「交付」）
2. 推 `confirmed_received` 前检查：本批计入收货的单位 **loading_line 非空比例**（缺则 `held_missing_loading_line`，不推进）。
3. `shipment_status_sync` 状态映射表补 `确认收货` / `已卸车` → delivered 语义。
4. 回归测试夹住上述行为。
5. 文档记录：箱级 95306 回写缺口作为 **后续工单**（本轮不扩写 sync 全链路，除非测试可小步）。

## 非目标

- 批量手工改现网 all_loaded 数据
- 重跑发运链 / 重传收货人
- 强制把未交付车辆改成已交付

## 验收

- pytest：closeout 扩展语义 + loading_line 闸 绿
- 工单结案并归档
- 现网 all_loaded 列表可作为观察基线，不在本工单强制清零
