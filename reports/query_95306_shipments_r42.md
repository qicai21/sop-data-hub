# Report: R42 — shared 95306 shipment query window executor

| 字段 | 内容 |
|------|------|
| Order | R42 |
| 执行日期 | 2026-05-29 |
| 执行者 | local Hermes |
| Branch | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

R42 完成。实现了跨项目复用的 `query_95306_shipments_by_window` 公共 executor，支持按时间窗口查询 95306 候选运单数据。14/14 新测试通过，全量 functional 176/176 通过。

## 2. 公共 executor 设计

```
departure_text (发运文本)
    ↓
reference_time (参考时间)
    ↓
QueryWindow (时间窗口: reference_time ± N min)
    ↓
query_95306_shipments_by_window()
    ↓
ShipmentQueryResult (候选运单列表)
    ↓
create_wagon_shipments (下一步接入)
```

**跨项目复用**:
- 吉林金钢 (jilin_jingang_jinzhou)
- 朝阳钢铁 (chaoyang_steel)
- 中唐特钢 (zhongtang_special_steel)
- 九三大豆 (jiusan)

所有项目共享同一套模型和查询逻辑。

## 3. 输入输出模型

### QueryWindow

```
reference_time: "2026-05-24 07:16:10"
window_before: 60 min
window_after: 60 min
→ start_time: "2026-05-24 06:16:10"
→ end_time:   "2026-05-24 08:16:10"
```

### ShipmentCandidate

95306 shipments 表字段映射：

| 输出字段 | 95306 来源 |
|----------|-----------|
| ydid | ydid |
| wagon_no | car_no |
| waybill_no | czydid |
| container_no | container_no_raw |
| origin_station | origin_name |
| destination_station | destination_name |
| cargo_name | cargo_name |
| ticketed_at | ticketed_at |
| departed_at | departed_at |
| arrived_at | arrived_at |
| delivered_at | delivered_at |
| current_status | latest_stage_name 或 status_name |

### ShipmentQueryResult

```
window: QueryWindow
origin_station, destination_station
cargo_name: optional filter
expected_car_count: optional (调用方参考)
total_candidates, exact_match_count, ambiguous_count
candidates: [ShipmentCandidate, ...]
```

## 4. 查询窗口逻辑

1. **reference_time** 解析为 datetime
2. **start_time** = reference_time - window_before_minutes
3. **end_time** = reference_time + window_after_minutes
4. SQL: `WHERE origin_name LIKE '%origin%' AND destination_name LIKE '%dest%' AND ticketed_at BETWEEN start AND end`
5. cargo_name 可选: `AND cargo_name LIKE '%cargo%'`
6. 只读: `file:path?mode=ro`

## 5. 真实数据验收（高桥镇→四平，铁矿粉）

使用真实 95306_collection.sqlite3 验证：

```bash
python scripts/query_95306_shipments.py \
  --origin 高桥镇 --destination 四平 \
  --reference-time "2026-05-24 07:16:10" \
  --window-before 10 --window-after 10 \
  --cargo-name 铁矿
```

**结果**:
- reference_time: 2026-05-24 07:16:10
- start_time: 2026-05-24 07:06:10
- end_time: 2026-05-24 07:26:10
- candidate_count: **28** (全部铁矿粉, TBJU 箱)
- 状态: 全部已交付
- 发车时间: 2026-05-24 11:24:00
- 到站时间: 2026-05-25 03:30:00
- 交付时间: 2026-05-25 20:25:10

样本 wagon_no: 1620389, 1821273, 1643820, ...

## 6. 测试结果

```
tests/functional/test_query_95306_shipments.py: 14 passed
tests/functional: 176 passed (无回归)
```

测试覆盖：

| # | 测试 | 状态 |
|---|---|---|
| 1 | QueryWindow 生成正确 | PASS |
| 2 | 前后 60 分钟窗口查询 | PASS |
| 3 | 查询返回 ShipmentCandidate | PASS |
| 4 | cargo_name 过滤 | PASS |
| 5 | expected_car_count 输出 | PASS |
| 6 | 无结果 | PASS |
| 7 | 多个候选 | PASS |
| 8 | 只读验证 (不修改 DB) | PASS |
| 9 | 不修改 sop_agent.db | PASS |
| 10 | 不修改 95306 DB 内容 | PASS |
| + | QueryWindow.to_dict | PASS |
| + | ShipmentCandidate.to_dict | PASS |
| + | ShipmentQueryResult.to_dict | PASS |
| + | 缺失 DB 路径不崩溃 | PASS |

## 7. 新增文件

| 文件 | 用途 |
|------|------|
| src/ops_hub/sop/shipment_query_window.py | QueryWindow, ShipmentCandidate, ShipmentQueryResult |
| src/ops_hub/sop/query_95306_shipments.py | query_95306_shipments_by_window() |
| scripts/query_95306_shipments.py | CLI |
| tests/functional/test_query_95306_shipments.py | 14 tests |

## 8. 下一轮: create_wagon_shipments 接入

R42 完成后，普通货运链路已具备:

```
departure_text → reference_time → QueryWindow → query_95306 → candidates
```

下一步 create_wagon_shipments 接入:

1. 输入: ShipmentQueryResult.candidates + release_batch_id
2. 处理: 将 candidate 映射为 wagon_shipments INSERT
3. 关联: batch_id → release_batches.id
4. 防重复: idempotent by ydid
5. 写入: sop_agent.db.wagon_shipments
