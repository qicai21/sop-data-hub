# Report: R41 — enrich_release_batch executor

| 字段 | 内容 |
|------|------|
| Order | R41 |
| 执行日期 | 2026-05-29 |
| 执行者 | local Hermes |
| Branch | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

R41 完成。实现了 enrich_release_batch executor，支持 FreightDetailCandidate → release_batch 的人工绑定流程。14/14 新测试通过，全量 functional 162/162 通过。

## 2. freight_detail_candidate → release_batch 人工绑定原则

freight_detail_text 的消息特征：

- 包含「订单标识」（CGR 前缀）+「合同号」/「入场合同号」
- 包含放货港、货名详情、数量（吨）
- **不包含船名** — 这是核心约束

由于没有船名，系统无法自动判断该放货详情应该绑定到 release_batches 中的哪条记录（不同船、不同批次可能有相同的合同号或订单标识）。因此：

1. 提取阶段（R40）：产出 `FreightDetailCandidate(status=complete, binding_status=needs_manual_binding)`
2. 绑定阶段（R41）：操作员/Agent 显式指定 `release_batch_id`，executor 执行回写
3. 后续阶段：release_batch 获得 order_identifier/contract_no/cargo_name_detail 后，可联动 95306 查询

## 3. 为什么不能自动按船名绑定

freight_detail_text 中不存在船名字段。分离 departure_text（有船名、有道/车数）和 freight_detail_text（有订单标识/合同号，无船名）是 R40 的核心设计决策。这两个信息源是互补的，但系统不能跨消息类型自动关联。

## 4. dry-run / apply 行为

### dry-run（默认）

- 解析 candidate，检查 release_batch_id 是否存在
- 检查 target columns 是否存在
- 返回 `planned_updates`（将要写入的字段）
- **不写 DB**

### apply

- 执行 dry-run 检查
- 对 `planned_updates` 中的每个字段，检查当前值是否非空
- 非空字段默认跳过（allow_overwrite=False）
- 写入剩余字段，同时更新 `updated_at`
- 返回 `applied_fields`

### 幂等

同一个 candidate 重复 apply 不会产生重复记录（SQL UPDATE 是幂等的）。第二次 apply 时所有字段已设置，返回 `no_op`。

### 不覆盖已有字段

```python
# 默认行为
enrich_release_batch_with_freight_detail(candidate, batch_id, dry_run=False)
# → 已有 contract_no 的行不会被覆盖

# 强制覆盖
enrich_release_batch_with_freight_detail(candidate, batch_id, dry_run=False, allow_overwrite=True)
# → 所有字段无条件写入
```

## 5. schema_missing_fields

新增 5 个 migration 列（`src/ops_hub/data_agent/db.py`）：

- `order_identifier` TEXT
- `cargo_name_detail` TEXT
- `quantity_tons` REAL
- `source_message_id` TEXT
- `source_group_id` TEXT

若 release_batches 表缺少任一 target column，executor 返回 `status=schema_missing_fields`，不抛异常。

## 6. 写入字段映射

| FreightDetailCandidate 字段 | release_batches 列 |
|---|---|
| order_identifier | order_identifier |
| contract_no | contract_no |
| cargo_name_detail | cargo_name_detail |
| quantity_tons | quantity_tons |
| message_id | source_message_id |
| group_id | source_group_id |

`updated_at` 始终更新为当前时间。

## 7. 测试结果

```
tests/functional/test_enrich_release_batch.py: 14 passed
tests/functional: 162 passed (full suite, no regressions)
```

测试覆盖：

| # | 测试 | 状态 |
|---|---|---|
| 1 | complete candidate → planned_updates | PASS |
| 2 | dry-run 不写 DB | PASS |
| 3 | apply 写 order_identifier | PASS |
| 4 | apply 写 contract_no | PASS |
| 5 | apply 写 cargo_name_detail | PASS |
| 6 | apply 写 quantity_tons | PASS |
| 7 | release_batch_id 不存在 → not_found | PASS |
| 8 | 已有非空字段默认不覆盖 | PASS |
| 9 | allow_overwrite=True 覆盖 | PASS |
| 10 | 重复 apply 幂等 | PASS |
| 11 | 缺列 → schema_missing_fields | PASS |
| 12 | 不修改 wagon_shipments | PASS |
| + | source_message_id/group_id 写入 | PASS |
| + | no_match candidate → no_op | PASS |

## 8. 新增文件

| 文件 | 用途 |
|------|------|
| src/ops_hub/sop/enrich_release_batch.py | 核心 executor |
| scripts/enrich_release_batch.py | CLI dry-run/apply |
| tests/functional/test_enrich_release_batch.py | 14 tests |

## 9. 修改文件

| 文件 | 变更 |
|------|------|
| src/ops_hub/data_agent/db.py | +5 migration columns |

## 10. 下一轮建议：公共 95306 查询窗口 executor

R41 完成后，release_batch 已具备 order_identifier / contract_no / cargo_name_detail。下一步可以实现一个公共的 95306 查询窗口 executor：

1. 输入：release_batch_id
2. 查询：用 order_identifier 或 car_numbers 从 95306 shipment_release_batch_matches 反查
3. 输出：发运状态摘要（已发运/未发运/部分发运车数）
4. 不写 DB，不修改 wagon_shipments
5. dry-run 模式，供操作员/Agent 决策参考
