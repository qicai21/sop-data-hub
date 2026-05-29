# R55: 蓝鳍 Lot02 45车全链路回归验证

**状态：** ✅ 全部通过  
**日期：** 2026-05-29  
**commit：** [pending]  
**分支：** `codex/sop-real-sop-topology-audit-20260525`

---

## 执行摘要

从原始微信消息 `wx_2206`（`煤六 45节 四平铁 蓝鳍`）重新触发完整生产链路，6 个步骤全部成功：

```
微信消息 → 解析 → 批次匹配 → 95306查询 → 车辆入库 →
  发运Excel生成 → 工厂系统上传
```

---

## Phase 1: 回滚状态

回滚前蓝鳍 lot02 已处于干净状态（R52/R53 均为 dry_run）：

| 表 | 回滚前 | 回滚后 |
|----|--------|--------|
| `wagon_shipments` (lot02) | 0 | 0 |
| `shipment_release_batch_matches` (lot02) | 0 | 0 |
| `release_batches` 状态 | `in_progress` / actual=0 | `in_progress` / actual=0 |

无需删除操作。

---

## Phase 2: 全链路触发

**触发命令：**

```bash
python scripts/run_live_service.py --once --sync-start-id 2206 --apply
```

**原始消息：**

| 字段 | 值 |
|------|-----|
| message_id | `wx_2206` |
| sender | 煦o.0 |
| time | 2026-05-29 14:58:24 |
| text | `煤六 45节 四平铁 蓝鳍` |

---

## Phase 3: 链路每步执行结果

### Step 1: parse_departure_text

| 指标 | 值 |
|------|-----|
| status | `complete` |
| destination | 四平 |
| car_count | 45 |
| lane_or_track | 煤六 |
| ship_name | 蓝鳍 |
| project_id | `jilin_jingang_jinzhou` |
| 耗时 | < 1s |

### Step 2: release_batch 匹配

| 指标 | 值 |
|------|-----|
| release_batch_id | `17860336195b8e6d419c2cc9993e3f1f2104f41e` |
| ship_name | 蓝鳍 |
| dispatch_status | `in_progress` |
| 匹配策略 | 蓝鳍 + 四平 → in_progress 优先 |
| 耗时 | < 0.1s（本地 DB 查询） |

### Step 3: query_95306

| 指标 | 值 |
|------|-----|
| origin_station | 高桥镇 |
| destination_station | 四平 |
| reference_time | 2026-05-29 14:58:24 |
| window | ±720min（2026-05-29 02:58:24 ~ 2026-05-30 02:58:24） |
| total_candidates | 45 |
| exact_match_count | 45 |
| ambiguous_count | 0 |
| expected_car_count | 45 |
| 匹配精度 | **100%（45/45 精确匹配）** |
| 耗时 | ~35s（95306 API 网络查询） |

### Step 4: create_wagon_shipments

| 指标 | 值 |
|------|-----|
| status | `safe_to_apply` |
| planned_insert_count | 45 |
| **实际 inserted_count** | **45** |
| skipped_existing_count | 0 |
| conflict_count | 0 |
| release_batch actual_wagon_count | 45 |
| release_batch dispatch_status | `in_progress` |
| 95306 DB | 只读，未写入 |
| 耗时 | ~5s（45 条 INSERT + shipment_matches + batch 更新） |

### Step 5: departure_excel

| 指标 | 值 |
|------|-----|
| 文件路径 | `output/excel/吉林金钢_发运数据_20260529_45车.xlsx` |
| 文件名 | `吉林金钢_发运数据_20260529_45车.xlsx` |
| sheet_name | `sheet1` |
| 标题 | `高桥镇装车发运明细`（A1:N1 合并） |
| 表头 | 14 列（序号 → 次数） |
| 数据行 | 45 行 |
| 车数 | 45 |
| cargo_name | 印粉（来源于 release_batch.cargo_name_detail） |
| ship_name | 蓝鳍 |
| contract_no | HNMC20260525-6X-1 |
| order_identifier | CGR20260526171655 |
| 装车日期 → 进场日期 | 2026-05-29 → 2026-06-28（+30d） |
| 双箱 | 同行分 container_no_1 / container_no_2 |
| 文件大小 | 8,630 bytes |
| 耗时 | ~1s |

### Step 6: factory_upload

| 指标 | 值 |
|------|-----|
| login | ✅ 成功（access_token 获取正常） |
| endpoint | `http://111.26.178.96:88/prod-api` |
| upload_path | `/sales/transportOrder/insert` |
| payload 总数 | **90**（45 车 × 双箱拆分） |
| 上传成功 | **90** |
| 上传失败 | **0** |
| 成功率 | **100%** |
| 耗时 | ~5s（login + 90 次 POST，每次 0.3s 间隔） |

---

## Phase 4: 数据库最终状态

| 表 | 触发前 | 触发后 | 变化 |
|----|--------|--------|------|
| `wagon_shipments` (lot02) | 0 | **45** | +45 |
| `shipment_release_batch_matches` (lot02) | 0 | **45** | +45 |
| `release_batches` actual_wagon_count | 0 | **45** | +45 |
| `wagon_shipments` (total) | 198 | **243** | +45 |
| `shipment_release_batch_matches` (total) | 28 | **73** | +45 |

---

## 13 项验收指标

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | 匹配到的 release_batch_id | ✅ `17860336195b8e6d419c2cc9993e3f1f2104f41e` |
| 2 | 匹配到的车辆数量 | ✅ 45 |
| 3 | 新增 wagon_shipments 数量 | ✅ 45 |
| 4 | 新增 shipment_release_batch_matches 数量 | ✅ 45 |
| 5 | 生成的发运Excel路径 | ✅ `output/excel/吉林金钢_发运数据_20260529_45车.xlsx` |
| 6 | Excel文件名 | ✅ `吉林金钢_发运数据_20260529_45车.xlsx` |
| 7 | Excel车数 | ✅ 45 |
| 8 | 生成的 factory payload 数量 | ✅ 90（双箱拆分） |
| 9 | 实际上传数量 | ✅ 90 |
| 10 | 上传成功数量 | ✅ 90 |
| 11 | 上传失败数量 | ✅ 0 |
| 12 | 工厂返回结果摘要 | ✅ 90/90 全部成功 |
| 13 | 95306 DB 未被写入 | ✅ 只读 mode=ro |

---

## 链路耗时分解

| 步骤 | 耗时 | 说明 |
|------|------|------|
| 1. parse_departure_text | < 1s | 本地正则 |
| 2. release_batch 匹配 | < 0.1s | 本地 SQLite |
| 3. query_95306 | ~35s | 95306 API 网络查询 |
| 4. create_wagon_shipments | ~5s | 45 INSERT + matches + batch update |
| 5. departure_excel | ~1s | openpyxl 生成 |
| 6. factory_upload | ~5s | login + 90 POSTs（0.3s 间隔） |
| **总计** | **~50s** | |

---

## 约束遵守

| 约束 | 状态 |
|------|------|
| 未直接调用 create_wagon_shipments_from_candidates() | ✅ 通过 live_service → executor_runner |
| 未直接调用 upload_release_batch() | ✅ 通过 executor_runner 自动串联 |
| 未直接生成 Excel | ✅ 通过 executor_runner 自动串联 |
| 从原始消息重新开始 | ✅ wx_2206 via live_service |
| 未 dry_run | ✅ --apply 模式 |
| 未 mock | ✅ 真实 95306 查询 + 真实工厂 POST |
| 未手工补数据库 | ✅ 全自动 |
| 95306 DB 只读 | ✅ mode=ro |
| 未修改 SOP YAML | ✅ |
| 未发送消息 | ✅ |
| 未刷新 dispatch_board | ✅ |

---

## 改动文件

| 文件 | 改动 |
|------|------|
| `src/ops_hub/sop/executor_runner.py` | 重写：添加 apply_mode + Excel + 工厂上传串联 |
| `scripts/run_live_service.py` | 添加 --apply flag，全链路透传 |

---

## 测试

```
216 passed
```

---

## 全链路触发命令

```bash
# Dry-run（默认）
python scripts/run_live_service.py --once --sync-start-id 2206

# Apply（真实写库 + Excel + 工厂上传）
python scripts/run_live_service.py --once --sync-start-id 2206 --apply
```

---

## 下一轮建议

- R56: 重复触发幂等性验证（同一消息 apply 两次应 skip 不重复插入）
- R57: 发运 Excel 自动发送到联系人郭东北
- R58: report_tasks 自动注册（Excel 生成后 register_report_task）
