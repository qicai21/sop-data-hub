# R36: Implement 95306 shipment status sync

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Task:** P0-6 partial — ship 95306 status into sop_agent.db for existing wagons

---

## 一、实现

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/ops_hub/sop/shipment_status_sync.py` | `ShipmentStatusSync`, `SyncResult`, `UpdatePlan` |
| `scripts/sync_shipment_status_from_95306.py` | CLI: `--dry-run` / `--apply` |
| `tests/functional/test_shipment_status_sync.py` | 10 tests |

### CLI

```
# Dry-run
python scripts/sync_shipment_status_from_95306.py \\
  --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --dry-run

# Apply
python scripts/sync_shipment_status_from_95306.py \\
  --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --apply
```

### 匹配优先级

1. `car_no` + `destination_name`（精确匹配）
2. `car_no` only（fallback）

### 状态映射

| 95306 latest_stage_name | 写入字段 |
|------------------------|---------|
| 发车 / 已发车 | `departed_at` |
| 到站 / 已到站 | `departed_at`, `arrived_at` |
| 交付 / 货物已交付 / 已交付 | `departed_at`, `arrived_at`, `delivered_at` |

---

## 二、蓝鳍 18 车结果

### dry-run

```
total_wagons:        18
matched_count:       18
unmatched_count:     0
update_count:        72  (18 × 4 fields)
departed_update:     18
arrived_update:      18
delivered_update:    18  (planned, schema-missing)
batch_level_suggestion: confirmed_received_candidate
schema_missing_fields: ["delivered_at"]
```

### --apply

```
departed_at:         18/18 ✅  (2026-05-23 06:20:00)
arrived_at:          18/18 ✅  (2026-05-24 00:59:00)
delivered_at:         0/18 ⚠️  列不存在，跳过
dispatch_status:     delivered ✅
```

### apply 后 DB 状态

```
wagon_shipments:
  departed_at = 2026-05-23 06:20:00  (18/18)
  arrived_at  = 2026-05-24 00:59:00  (18/18)

release_batches:
  dispatch_status = delivered
  dispatch_status_updated_at = 2026-05-28 08:16:16
```

---

## 三、测试

```
pytest tests/functional/test_shipment_status_sync.py -v
10 passed

pytest tests/functional -v
123 passed (48 legacy + 7 R32 + 19 R33 + 23 R34 + 16 R35 + 10 R36)
```

### 测试覆盖

| # | Test | 验证内容 |
|---|------|---------|
| 1 | test_match_by_car_no | car_no 匹配 |
| 2 | test_departed_at_written | 发车状态写 departed_at |
| 3 | test_arrived_at_written | 到站状态写 arrived_at |
| 4 | test_dispatch_status_delivered | 交付→batch dispatch_status=delivered |
| 5 | test_dry_run_no_write | dry-run 不写库 |
| 6 | test_apply_writes | apply 写库 |
| 7 | test_dont_overwrite_existing | 不覆盖已有非空值 |
| 8 | test_schema_missing_delivered_at | 缺列返回 schema_missing_fields |
| 9 | test_rail_db_not_modified | 95306 DB 保持只读 |
| 10 | test_unmatched_wagon | 未匹配 wagon 统计 |

---

## 四、缺列处理

`wagon_shipments` 表缺少 `delivered_at` 列：

- **dry-run**: 在 `updates` 中列出 planned delivered_at 更新，`schema_missing_fields=["delivered_at"]`
- **apply**: 跳过 `delivered_at` 列的 UPDATE，只写 `departed_at` / `arrived_at`

如需写入 `delivered_at`，需先执行 DB migration：`ALTER TABLE wagon_shipments ADD COLUMN delivered_at TEXT`。

---

## 五、batch_level_suggestion

全部 18 车交付后 → `confirmed_received_candidate`。
`release_batches.dispatch_status` 已设为 `delivered`。
未设为 `confirmed_received` — 需确认逻辑（SOP 要求 auto/manual 双重确认）。

---

## 六、如何挂成定时任务

```bash
# 每 30 分钟检查一次
python scripts/sync_shipment_status_from_95306.py \\
  --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --apply
```

可注册为 Hermes cronjob:

```json
{
  "schedule": "30m",
  "prompt": "sync 蓝鳍 95306 status",
  "script": "scripts/sync_shipment_status_from_95306.py --project-id jilin_jingang_jinzhou --ship-name 蓝鳍 --apply",
  "no_agent": true
}
```

或按 ship_name 轮询所有 active release_batches。

---

## 七、未做

- 不添加 `delivered_at` 列（需用户决策）
- 不标记 `confirmed_received`（需用户确认逻辑）
- 不生成 Excel / JSON
- 不改 jilin_jingang.yaml
- 不写 95306_collection.sqlite3
- 不改 wx-ops-agent
- 不注册 cron（用户后续处理）

---

## 八、文件

- `src/ops_hub/sop/shipment_status_sync.py` — 新
- `scripts/sync_shipment_status_from_95306.py` — 新
- `tests/functional/test_shipment_status_sync.py` — 新（10 tests）
