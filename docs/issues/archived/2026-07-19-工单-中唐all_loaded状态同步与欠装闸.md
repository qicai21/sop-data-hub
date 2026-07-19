# 工单：中唐 all_loaded 95306 状态同步 + 欠装闸修正

- **状态**：已完成
- **日期**：2026-07-19

## 做了什么

1. 备份后对中唐船（丰收散运/宝丽/环球信任/贝拉）apply `ShipmentStatusSync`
2. closeout 发现 4 批已全交付仍 `held_underfilled`（remaining>100）
3. **修复**：散粮欠装闸仅约束 `loading`，**不**约束已 `all_loaded`
4. 再跑 closeout：宝丽 lot01/02、贝拉 lot05、丰收 lot08、环球 lot08 → `confirmed_received`
5. 中唐 `all_loaded` 清空

## 测试

`tests/test_lifecycle_closeout_bulk_gate.py::test_all_loaded_bulk_not_held_by_remaining_tons`

## 备份

`data/backups/sop_agent_zhongtang_status_sync_20260719_143137.sqlite3`
