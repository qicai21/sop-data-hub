# 工单：all_loaded「停车场」与交付确认推进

- **提出日期**：2026-07-19
- **状态**：已完成（代码层；现网清停车场另议）
- **来源**：用户转述 Claude 评审结论 + 本轮库内只读盘点
- **范围**：lifecycle closeout / 95306 状态语义；装车道线完备性；**不**重发 Excel、**不**重传门户

## 现象 / 背景

- Claude 结论：`all_loaded` 成了大型停车场，业务终点应是 **确认交付**。
- 用户可接受状态：`货物已交付` / `确认收货` / `已卸车`；并要求 **装车道线** 齐全。

## 只读盘点摘要

- 吉林 5 / 中唐 5 批停在 `all_loaded`。
- 车级多仍为已制单/已发车；吉林箱级 `latest_stage_key` 大量为空 → closeout 无法 all_received。
- 蓝鳍等批 `loading_line` 缺失比例高。

## 修复

1. **closeout 收货判定**扩展：
   - `latest_stage_key ∈ {delivered, unloading_completed}` **或**
   - `status_name ∈ {货物已交付, 确认收货, 已卸车, 已交付, 交付}`
2. **装车道线闸**：全收货后若仍有空 `loading_line` → `held_missing_loading_line`，**不**推 `confirmed_received`。
3. **shipment_status_sync** 映射表补 `确认收货` / `已卸车`。
4. 陈旧测试修正：吉林 closeout 只认箱级表；`test_lifecycle_closeout_advances_when_all_wagons_delivered` 改为箱级 fixture + loading_line。

## 后续（未在本工单做）

- 箱级表 95306 状态回写主路径仍偏弱（stage 空）→ 建议新开：`wagon_container_shipments` 状态同步与 rail95306 对齐。
- 现网 all_loaded 批 **不** 本工单批量改状态（无交付事实不得假推进）。

## 验证

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_lifecycle_closeout_bulk_gate.py \
  tests/functional/test_inbox_retry_and_cross_month_keys.py \
  tests/functional/test_lifecycle_closeout.py -q
```

## 结案

- 代码：`lifecycle_closeout.py`、`shipment_status_sync.py`
- 测试：bulk_gate 新增 3 项；inbox 吉林 fixture 更新
- 未改业务数据、未发单
- text-watch 加载 closeout 的进程建议 kickstart 后生效
- 归档：`docs/issues/archived/`
