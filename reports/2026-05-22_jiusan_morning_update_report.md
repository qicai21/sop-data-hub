# 九三大豆晨报更新报告 — 2026-05-22

## 概况

| 项目 | 值 |
|------|-----|
| 日期 | 2026-05-22 |
| 项目 | 九三大豆铁路发运 / 九三铁岭大豆循环运输 |
| 船 | 玛格丽特 |
| 系统总箱 | 650（←21日613，+37 candidate） |

## 港口库存（5/22晨报）
- 重箱：170
- 空箱：0

## 昨日作业（5/21）
- 装车：161
- 发出：208

## 到站库存
| 地点 | 重箱 | 空箱 |
|------|------|------|
| 新台子 | 130 | 0 |
| 三三〇处 | 114 | 132 |

## 昨日两列车确认（人工）

| 项目 | 7道 | 8道 |
|------|-----|-----|
| 车数 | 52 | 52 |
| 箱数 | 104 | 104 |
| 制票 | 11:09~11:13 | 15:45~15:52 |
| 高桥镇发车 | 16:15 | 20:47 |
| 经裕国 | 21:18~22:36 | 22日01:25~01:41 |
| 到新台子 | 23:45 | 22日04:46 |
| 状态 | 39车到站待卸，13车已卸 | 52车已到站待卸 |
| 合计 | 104车/208箱全部到站 |

## 返空
- 52车/104空箱（列1返程）
- 尚未到高桥镇站
- 状态：返程在途

## 项目总箱资源

| 分项 | 数量 |
|------|------|
| 港口重箱 | 170 |
| 新台子重箱 | 130 |
| 三三〇重箱 | 114 |
| 三三〇空箱 | 132 |
| 返程在途 | 104 |
| **系统总箱** | **650** |

## 资源调整
- 相对21日：613 → 650
- 差：+37
- 标记：`resource_adjustment_candidate`（confidence=medium）
- 未直接覆盖历史

## 数据库更新
- `jiusan_cycle_trains`：train_03 updated（forming→arrived, 8道52车）
- `jiusan_cycle_trains`：train_04 created（7道52车, arrived）
- `jiusan_cycle_train_runs`：两列运行记录写入
- `jiusan_snapshots`：snap_20260522_morning 写入
- `jiusan_flows`：flow_20260522_morning 写入
- `jiusan_adjustments`：adj_container_20260522_candidate 写入（+37, medium）
- `jiusan_resource_pool`：container_pool 更新至319，wagon_pool 更新至104
- `jiusan_dashboard_data.json`：全量更新

## 看板数据
- JSON：`dashboard/jiusan_dashboard_data.json` ✓
- HTML：刷新时需执行 `python3 scripts/jiusan_refresh_board.py`

## 待确认
1. **resource_adjustment_candidate +37**：是否正式记为 inventory_gain？
2. **返空到港时间**：52车/104空箱到高桥镇后需记录
3. **后续循环安排**：列1~列4全部到站/回程，下一轮是否重组
4. **库存预警**：可用粮3200吨，仅支撑0.6天
