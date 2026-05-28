# R39: SOP-driven ordinary freight DB schema migration

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Type:** Schema migration + model alignment — 不新增业务逻辑

---

## 1. 状态确认

```
live_service --status: source_of_truth=git, loaded_projects=4, jilin_jingang_jinzhou ✅
pytest tests/functional -v: 133 passed, 0 failed
```

---

## 2. Schema 变更对比

### 2.1 migration 前后 schema

| 表 | 字段 | 类型 | 迁移前 | 迁移后 |
|---|---|---|---|---|
| **wagon_shipments** | `delivered_at` | TEXT | ❌ | ✅ |
| | `confirmed_received_at` | TEXT | ❌ | ✅ |
| | `container_no` | TEXT | ❌ | ✅ |
| | `waybill_no` | TEXT | ❌ | ✅ |
| **release_batches** | `order_identifier` | TEXT | ❌ | ✅ |
| | `cargo_name_detail` | TEXT | ❌ | ✅ |
| | `confirmed_received_at` | TEXT | ❌ | ✅ |
| **dashboard_state** | (entire table) | — | ❌ | ✅ CREATE |

### 2.2 新增字段

| 表.字段 | 用途 | 映射来源 |
|---|---|---|
| `wagon_shipments.delivered_at` | 95306 交付时间 | SOPShipment → delivered_at |
| `wagon_shipments.confirmed_received_at` | 收货方确认时间 | 默认规则: 交付=确认; 可被SOP覆盖 |
| `wagon_shipments.container_no` | 集装箱号 | SOPShipment → container_no |
| `wagon_shipments.waybill_no` | 铁路运单号 | SOPShipment → waybill_no |
| `release_batches.order_identifier` | 订单标识号 | freight_detail_text → order_identifier |
| `release_batches.cargo_name_detail` | 货物品名明细 | freight_detail_text → cargo_name_detail |
| `release_batches.confirmed_received_at` | 全批次确认时间 | 默认规则自动写入 |
| `dashboard_state` | SOP 跟踪看板状态表 | 全表新建: id, project_id, release_batch_id, total_wagon_count, dispatched/arrived/delivered/confirmed_received_count, status, last_updated_at |

### 2.3 未删除字段

零列被删除。仅 `ALTER TABLE ADD COLUMN` + `CREATE TABLE IF NOT EXISTS`。

---

## 3. 幂等性验证

```
# First apply:  added=7 columns + 1 table
# Second apply: added=0, skipped=7, error=""
```

✅ 重复执行无报错、无重复添加。

---

## 4. 蓝鳍 18 车验收

### 4.1 执行命令

```bash
python scripts/run_db_migration.py --apply
python scripts/sync_shipment_status_from_95306.py \
  --project-id jilin_jingang_jinzhou \
  --ship-name 蓝鳍 --apply
```

### 4.2 结果

| 字段 | 填充数 | 状态 |
|---|---|---|
| `departed_at` | 18/18 | ✅ (R36 已有) |
| `arrived_at` | 18/18 | ✅ (R36 已有) |
| `delivered_at` | 18/18 | ✅ **新增 (R39)** |
| `confirmed_received_at` | 18/18 | ✅ **新增 (R39)** |

**Sample:** `car_no=1511993`, `departed=2026-05-23 06:20:00`, `arrived=2026-05-24 00:59:00`, `delivered=2026-05-24 15:35:46`, `confirmed=2026-05-24 15:35:46`

### 4.3 release_batch 级结果

```
release_batch.dispatch_status = delivered
release_batch.confirmed_received_at = 2026-05-24 15:35:46
```

---

## 5. 默认规则说明

### R39 默认规则

> **若 SOP 未声明额外人工确认规则，则 95306 已交付 (交付/货物已交付/已交付) 视为 confirmed_received。**

实施位置: `src/ops_hub/sop/shipment_status_sync.py` `_apply_updates()`:

```python
# R39: Default confirmed_received rule
# If ALL wagons have delivered_at updates, auto-set
# confirmed_received_at on each wagon AND on the release_batch.
```

触发条件:
1. `confirmed_received_at` 列存在
2. 所有 wagon 的 95306 状态均为 "交付/货物已交付/已交付"
3. 每个 wagon 写入 `confirmed_received_at = delivered_at`
4. release_batch 写入 `confirmed_received_at = last_delivered_at`

**未来可被 SOP YAML override:**
- 若 SOP 声明 `confirmed_received_requires_manual: true`，则跳过自动写入，状态标记为 `pending_confirmation`
- 若 SOP 声明 `confirmed_received_mapping: {交付: by_customer, 到站: auto}`，则按映射执行

当前吉林金钢 SOP 未声明额外人工确认规则，因此使用默认规则。

---

## 6. 新增/修改文件

### 新增

| 文件 | 描述 |
|---|---|
| `migrations/20260528_r39_ordinary_freight_schema.sql` | SQL 迁移文档 |
| `scripts/run_db_migration.py` | 迁移 runner: --dry-run / --apply, 幂等 |
| `tests/functional/test_db_schema_migration_r39.py` | 10 个测试 |

### 修改

| 文件 | 变更 |
|---|---|
| `src/ops_hub/sop/shipment_status_sync.py` | `_safe_col()` + `_release_batch_columns()`; `_load_wagons` 读 delivered_at; `_apply_updates` 写 confirmed_received_at; 默认规则文档 |

---

## 7. 下一轮建议

| 轮次 | 任务 | 优先级 |
|:--:|------|:--:|
| R40 | `enrich_release_batch` executor — 从 freight_detail_text 提取 order_identifier/cargo_name_detail/contract_no 写入 release_batches | P0 |
| R41 | `build_time_window` executor — departure_text → ±60m 95306 查询窗口 | P0 |
| R42 | `query_95306_waybills` + `create_wagon_shipments` executor | P0 |
| R43 | 95306 poller cron job — 定时运行 shipment_status_sync | P0 |
| R44 | 全船验收: 木森17, 长航滨海, 贝拉 | P1 |

---

## 8. 测试

```bash
pytest tests/functional/test_db_schema_migration_r39.py -v  # 10 passed
pytest tests/functional -v                                    # 133 passed, 0 failed
```
