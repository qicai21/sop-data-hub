# Report: R45 — create_wagon_shipments executor

| 字段 | 内容 |
|------|------|
| Order | R45 |
| 执行日期 | 2026-05-29 |
| Branch | codex/sop-real-sop-topology-audit-20260525 |
| Tests | 207 passed, 0 failed |

## 1. 结论

R45 完成。**departure_flow 的最后一个 P0 缺口已封闭。** `create_wagon_shipments` executor 实现，支持 departure_text → 95306 query → wagon_shipments.write 全链路。

implemented: 9→12, missing: 12→9。

## 2. create_wagon_shipments 设计

### 架构

```
departure_text → parse → QueryWindow → query_95306 → candidates
                                                        ↓
                                    create_wagon_shipments_from_candidates()
                                                        ↓
                                    ┌─ dry_run → plan only
                                    ├─ apply → INSERT wagon_shipments
                                    ├─ apply → UPDATE release_batches.actual_wagon_count
                                    └─ apply → INSERT shipment_release_batch_matches
```

### 输入

| 参数 | 类型 | 说明 |
|------|------|------|
| release_batch_id | str | 显式指定，不自动推断 |
| departure_candidate | DepartureCandidate | 解析后的发运文本 |
| shipment_query_result | ShipmentQueryResult | R42 查询结果 |
| dry_run | bool | 默认 True，不写 DB |
| allow_partial | bool | 允许 candidate < car_count |
| allow_existing_skip | bool | 跳过已有 wagon |

### 输出

```
status: safe_to_apply | pending_review | not_found | no_candidates
safe_to_apply: True/False
planned_insert_count / inserted_count / skipped_existing_count / conflict_count
warnings / schema_missing_fields
plans: [WagonPlan(action=insert|skip_existing|conflict_other_batch), ...]
release_batch_progress: {actual_wagon_count: N, dispatch_status: ...}
```

## 3. 支持同船多次发运

**设计原则**：`wagon_shipments.batch_id` 允许多次写入，不要求一次性。

```
蓝鳍第一次: 18 cars → batch_id=rb_001 → wagon_count=18
蓝鳍第二次: 28 cars → batch_id=rb_001 → wagon_count=18+28=46
```

**幂等保证**：
- 按 `car_no` 检查已有 wagon_shipments
- 已有则 skip（计入 skipped_existing_count）
- 使用确定性 ID（SHA1: ydid|release_batch_id）

**跨批次冲突**：
- 按 `car_no` 查询所有 wagon_shipments
- 属于其他 `batch_id` 的车号 → conflict（计入 conflict_count，不覆盖）

## 4. 数量匹配规则

| 场景 | 状态 | 说明 |
|------|:--:|------|
| candidate_count == car_count | safe_to_apply | 精确匹配 |
| candidate_count < car_count | pending_review | 除非 allow_partial=True |
| candidate_count > car_count | pending_review | 先尝试 destination/cargo 过滤；过滤后仍多→review |
| candidate_count + skipped == car_count | safe_to_apply | 已有部分 wagon 已存在 |
| expected_car_count = -1 | safe_to_apply | 无预期车数，全量接受 |

## 5. 蓝鳍 18 + 28 = 46 处理逻辑

### 第一次发运（18 车）

```
departure_text: "蓝鳍 18节"
reference_time: <第一次发运时间>
95306 query: 返回 18 candidates
create_wagon_shipments:
  - planned_insert: 18
  - candidate_count: 18
  - car_count: 18 → safe_to_apply ✅
```

### 第二次发运（28 车）

```
departure_text: "蓝鳍 28节"
reference_time: <第二次发运时间>
95306 query: 返回 28 candidates
create_wagon_shipments:
  - 检查已有: 18 cars → skipped_existing_count: 0（不同 ydid，不同车号）
  - 无跨批次冲突
  - planned_insert: 28
  - candidate_count: 28
  - car_count: 28 → safe_to_apply ✅
```

### 结果

```
release_batches.actual_wagon_count: 18 + 28 = 46
wagon_shipments SELECT COUNT(*): 46
```

## 6. 真实数据 dry-run 结果

### 蓝鳍 28 车批次（2026-05-24）

```bash
python scripts/create_wagon_shipments.py \
  --release-batch-id 916ec02cd1ef23302a533a3cac109953ac47d310 \
  --departure-text '煤六 四平铁 蓝鳍 28节' \
  --reference-time "2026-05-24 07:16:10" \
  --origin 高桥镇 --destination 四平 \
  --cargo-name 铁矿 --dry-run
```

**结果**:
- status: `safe_to_apply`
- candidate_count: 28, expected_car_count: 28
- planned_insert_count: 28
- 0 skipped, 0 conflicts
- release_batch_progress: actual_wagon_count=46

### 蓝鳍用 18 节 departure text 查询 28 车批次

**结果**:
- status: `pending_review`
- candidate_count: 28, expected_car_count: 18
- planned_insert_count: 28 > 18
- Warning: "Planned insert (28) > expected (18). Filtering may be needed."

### schema_missing_fields

生产 DB 未迁移 wagon_shipments 新列：
- `column:wagon_shipments.project_id`
- `column:wagon_shipments.ship_name`
- `column:wagon_shipments.dispatch_status`
- `column:wagon_shipments.source_message_id`
- `column:wagon_shipments.source_group_id`

migration 代码已添加至 `db.py:migrate_wagon_shipments_schema()`，下次 `open_db()` 或首轮 apply 时自动添加。

## 7. 是否仍有 pending_review 情况

**有**，且这是正常设计：

1. candidate_count > car_count → 需要操作员确认过滤
2. candidate_count < car_count → 需要确认是否 allow_partial
3. 跨批次冲突 → 需要操作员处理

**不是 pending_review 的情况**：
1. candidate_count == car_count → 直接 safe_to_apply
2. candidate + existing == car_count → 直接 safe_to_apply（累计发运）
3. allow_partial=True 且检测到部分匹配 → safe_to_apply

## 8. executor registry 更新

| executor | 状态 |
|----------|:--:|
| `create_wagon_shipments` | implemented ✅ |
| `bind_wagons_to_release_batch` | implemented ✅ |
| `check_existing_wagon_shipments` | implemented ✅ |

**新的 implemented 总数**: 9→**12**

## 9. DB schema migration

新增 `migrate_wagon_shipments_schema()`:
- 创建表（如不存在）
- 添加列：delivered_at, confirmed_received_at, container_no, waybill_no, project_id, ship_name, dispatch_status, source_message_id, source_group_id

## 10. 测试结果

```
tests/functional/test_create_wagon_shipments.py: 15 passed
tests/functional: 207 passed, 0 failed
```

| # | 测试 | 状态 |
|---|------|:--:|
| 1 | candidate_count == car_count → safe_to_apply | PASS |
| 2 | dry-run 不写 DB | PASS |
| 3 | apply 写入 wagon_shipments | PASS |
| 4 | 写入 waybill/wagon/container | PASS |
| 5 | 重复 apply 幂等 skip | PASS |
| 6 | 18+28 累计 46 | PASS |
| 7 | 跨批次冲突 | PASS |
| 8 | candidate < expected → pending_review | PASS |
| 9 | candidate > expected 过滤 | PASS |
| 10 | allow_partial | PASS |
| 11 | release_batch not found | PASS |
| 12 | no_candidates | PASS |
| 13 | 更新 actual_wagon_count | PASS |
| 14 | 累计更新 wagon_count | PASS |
| 15 | 不修改 95306 DB | PASS |

## 11. 新增文件

| 文件 | 用途 |
|------|------|
| src/ops_hub/sop/create_wagon_shipments.py | 核心 executor |
| scripts/create_wagon_shipments.py | CLI |
| tests/functional/test_create_wagon_shipments.py | 15 tests |

## 12. 修改文件

| 文件 | 变更 |
|------|------|
| src/ops_hub/data_agent/db.py | +migrate_wagon_shipments_schema() |
| src/ops_hub/sop/sop_task_compiler.py | +3 executor status entries |
| tests/functional/test_sop_task_compiler.py | +1 test assertion update |
| tests/functional/test_executor_registry_status_r44.py | +3 test assertion updates |

## 13. 下一轮建议

R45 完成后，departure_flow 的写入链已完整：
```
departure_text → parse → QueryWindow → 95306 query → create_wagon_shipments → DB
```

建议 R46：
- 消息去重 (group_id + seq)
- 定时 95306 轮询 cron
- 蓝鳍/长航滨海真实 apply 验收
