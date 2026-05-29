# R53: Live Service 蓝鳍 Lot2 全链条触发验证

**状态：** ✅ 通过  
**日期：** 2026-05-29  
**分支：** `codex/sop-real-sop-topology-audit-20260525`

---

## 变更摘要

将 `executor_runner` 接入 `live_service` 的 `process_event_once` 管道，用蓝鳍 lot2 真实发车消息验证全链条触发。

### 改动文件

| 文件 | 改动 |
|------|------|
| `scripts/run_live_service.py` | +25 lines（import + executor_runner hook） |

---

## 服务启动命令

```bash
cd /Users/qicai21/projects/repos/sop-data-hub
python scripts/run_live_service.py --once --sync-start-id 2206
```

退出码：0

---

## 触发消息

### 蓝鳍 lot2 发车消息（wx_2206）

| 字段 | 值 |
|------|-----|
| message_id | `wx_2206` |
| group_id | `铁晟业务工作群` |
| sender | `煦o.0` |
| time | `2026-05-29 14:58:24` |
| text | `煤六 45节 四平铁 蓝鳍` |

### 蓝鳍 lot2 放货消息（wx_2209）— 对照组

| 字段 | 值 |
|------|-----|
| message_id | `wx_2209` |
| sender | `赛彬` |
| text | `四平放货蓝鳍3000吨` |

---

## Parse 结果

### wx_2206 → 全链条成功

```
parse_departure_text:
  status:        complete
  destination:   四平
  car_count:     45
  lane_or_track: 煤六
  ship_name:     蓝鳍
  project_id:    jilin_jingang_jinzhou
```

### wx_2209 → 正确跳过

```
parse_departure_text:
  status:        incomplete
  car_count:     -1  (非 departure 文本，car_count 无法提取)
  skipped:       "departure incomplete: car_count=-1"
```

---

## 95306 查询结果（wx_2206）

| 字段 | 值 |
|------|-----|
| origin_station | 高桥镇 |
| destination_station | 四平 |
| reference_time | 2026-05-29 14:58:24 |
| window | ±720min（2026-05-29 02:58:24 ~ 2026-05-30 02:58:24） |
| total_candidates | 45 |
| exact_match_count | 45 |
| ambiguous_count | 0 |
| expected_car_count | 45 |

**匹配精确：45/45**，无歧义。

---

## Wagon Dry-Run 结果（wx_2206）

| 字段 | 值 |
|------|-----|
| status | `safe_to_apply` |
| planned_insert_count | 45 |
| inserted_count | 0（dry_run） |
| conflict_count | 0 |
| warnings | [] |
| release_batch_id | `17860336195b8e6d419c2cc9993e3f1f2104f41e`（蓝鳍 lot02, in_progress） |
| dispatch_status_updated_at | 2026-05-29T08:58:53Z |

---

## Preview 文件路径

| 文件 | 大小 | 内容 |
|------|------|------|
| `runtime/execution_previews/wx_2206.json` | 10.7KB | 完整 4-step 链路结果，45 wagon plans |
| `runtime/execution_previews/wx_2209.json` | 1.0KB | skipped（freight_detail，非 departure） |

---

## 全部 12 条验证结论

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | live_service 启动成功 | ✅ exit 0，pid 写入 |
| 2 | 蓝鳍 lot2 发车消息 wx_2206 被触发 | ✅ |
| 3 | parse_departure_text 正确解析 | ✅ complete, 45 cars, 煤六, 蓝鳍, 四平 |
| 4 | release_batch 正确匹配蓝鳍 lot02 | ✅ `17860336195...`, in_progress |
| 5 | query_95306 正确查询 | ✅ 45 candidates, ±720min, exact match |
| 6 | create_wagon_shipments dry_run | ✅ safe_to_apply, 45 planned, 0 inserted |
| 7 | execution_previews JSON 生成 | ✅ wx_2206.json (10.7KB), wx_2209.json (1.0KB) |
| 8 | 日志中记录 executor_runner 成功 | ✅ "executor_runner persisted ... parse=complete query=45 wagons=safe_to_apply planned=45" |
| 9 | 非 departure 消息正确跳过 | ✅ wx_2209 "departure incomplete: car_count=-1" |
| 10 | 非吉林金钢消息不触发 95306 | ✅ 无朝阳/九三/中唐消息误触发 |
| 11 | DB 未被写入（dry_run） | ✅ lot02 batch wagon count = 0 |
| 12 | 不发送消息 | ✅ |

---

## 全链条触发确认

```
微信消息 "煤六 45节 四平铁 蓝鳍"
  ↓ live_service 检测（含 "四平" 关键词）
  ↓ executor_runner.run_departure_executor_chain_if_applicable()
  ↓ parse_departure_text → complete (45 cars, 蓝鳍, 四平)
  ↓ _find_release_batch("蓝鳍", "四平") → lot02 (in_progress)
  ↓ query_95306_shipments_by_window(高桥镇→四平, ±720min) → 45 exact match
  ↓ create_wagon_shipments_from_candidates(dry_run=True) → safe_to_apply, 45 planned
  ↓ runtime/execution_previews/wx_2206.json (10.7KB)
```

**链路上每个环节都完成了实际调用，返回真实数据，无 mock。**

---

## 为什么不是 lot（列次）

按照 order 要求，发车信息使用 car/wagon 语义，不叫 lot。

---

## 下一步

- **R54: 受控 apply**：基于当前 dry-run 验证通过，用户确认后可进入 apply 模式写库
- **R41 enrich_release_batch**：将 wx_2209（四平放货蓝鳍3000吨）的 freight_detail 绑定到 lot02
- **R42: 公共 95306 查询窗口 executor**：抽离 query 为独立可复用 executor

---

## 测试命令

```bash
cd /Users/qicai21/projects/repos/sop-data-hub
python scripts/run_live_service.py --once --sync-start-id 2206
```
