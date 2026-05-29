# Report: Live Executor Runner — R52

| 字段 | 内容 |
|------|------|
| Order ID | R52 |
| 执行日期 | 2026-05-29 |
| 执行者 | local Hermes |
| branch | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

**live_service 现在可以从真实微信消息进入 executor 链。**

新增 `executor_runner` 模块，在 `process_event_once` 末尾挂载，串联三个已实现的 executor（dry-run only）：

```
微信消息 → parse_departure_text → query_95306 → create_wagon_shipments(dry_run=True)
```

preview JSON 写入 `runtime/execution_previews/`。不写 sop_agent.db，不刷新看板，不发送消息。

## 2. 实现

### 2.1 新模块：`src/ops_hub/sop/executor_runner.py`

| 函数 | 用途 |
|------|------|
| `run_departure_executor_chain(event, *, runtime_root, db_path)` | 完整 executor 链，always dry-run |
| `run_departure_executor_chain_if_applicable(event, *, runtime_root, db_path)` | 快速预检：非四平/空文本 → None |

**执行逻辑：**

```
1. parse_departure_text(event) → DepartureCandidate
   ├─ no_match / not jilin_jingang / incomplete → 写 skipped preview，返回
   └─ complete →
     2. _find_release_batch(ship_name, destination) → release_batch_id
        ├─ not found → 写 skipped preview，返回
        └─ found →
          3. query_95306_shipments_by_window(高桥镇, 四平, ±720min)
          4. create_wagon_shipments_from_candidates(dry_run=True)
          5. 写 execution_preview JSON
```

**关键设计决策：**

- origin_station: `高桥镇`（95306 DB 实际站名，非消息原文"锦州港"）
- window: ±720 分钟（微信消息时间与货运票据时间常有数小时偏差）
- 仅处理 `jilin_jingang_jinzhou`，跳过其他项目
- DB path: 使用 canonical sop-data-hub 路径（非 cwd）

### 2.2 live_service 集成

`scripts/run_live_service.py` 两处改动：

1. **import** (L40): `from ops_hub.sop.executor_runner import run_departure_executor_chain_if_applicable`
2. **hook** (L238-255): 在 `process_event_once` 末尾，写成 try/except 包裹的 executor 调用

```python
try:
    exec_preview = run_departure_executor_chain_if_applicable(event, runtime_root=runtime_root)
    if exec_preview is not None and not exec_preview.skipped_reason:
        logger.info("executor_runner message_id=%s ... wagons=%s/%d", ...)
    elif exec_preview is not None:
        logger.info("executor_runner skipped message_id=%s reason=%s", ...)
except Exception as exc:
    logger.error("executor_runner failed message_id=%s: %s", event.message_id, exc)
```

## 3. 验收

### 3.1 蓝鳍 — 完整链路（wx_2206）

```
消息: "煤六 45节 四平铁 蓝鳍"
```

| 步骤 | 结果 |
|------|------|
| parse_departure_text | ✅ status=complete, dest=四平, cars=45, ship=蓝鳍 |
| find_release_batch | ✅ 蓝鳍 lot02 (in_progress) |
| query_95306 (±720min, 高桥镇) | ✅ 45 candidates |
| create_wagon_shipments (dry_run) | ✅ status=safe_to_apply, 45 planned |
| preview 文件 | ✅ wx_2206.json (10.7KB) |

### 3.2 蓝鳍 — freight_detail 跳过（wx_2209）

```
消息: "四平放货蓝鳍3000吨"
```

| 步骤 | 结果 |
|------|------|
| parse_departure_text | car_count=-1, status=incomplete |
| executor_runner | "skipped: departure incomplete: car_count=-1" |
| preview 文件 | ✅ wx_2209.json (1KB, skipped) |

这是正确行为 — "四平放货蓝鳍3000吨" 是 freight_detail（放货信息，可能没有车数），不是 departure text。

### 3.3 长航滨海 — 完整链路（模拟）

```
消息: "6道，四平方向，长航滨海，46车" (2026-05-21 10:35)
```

| 步骤 | 结果 |
|------|------|
| parse | ✅ complete, 46 cars, 长航滨海 |
| query_95306 | ✅ 46 candidates |
| wagons (dry_run) | ✅ safe_to_apply, 46 skipped_existing (已在 DB) |

### 3.4 live_service 集成

```
$ python scripts/run_live_service.py --sync-start-id 2206 --once

executor_runner message_id=wx_2206 status=complete depart=四平 query=45 wagons=safe_to_apply/45
executor_runner skipped message_id=wx_2209 reason=departure incomplete: car_count=-1
```

**live_service 不再停在 preview 层** — 消息匹配后自动进入 executor 链。

## 4. 约束遵守

| 约束 | 状态 |
|------|------|
| dry-run only，不写 sop_agent.db | ✅ 所有 create_wagon_shipments 调 dry_run=True |
| 不写 95306 DB | ✅ query_95306 使用 mode=ro |
| 不处理朝阳 | ✅ 仅 jilin_jingang_jinzhou |
| 不刷新正式看板 | ✅ 不调 refresh_dispatch_board |
| 不发送消息 | ✅ 不调 send_excel / telegram |
| 不改 SOP YAML | ✅ |

## 5. 测试

```
tests/functional/test_sop_task_compiler.py ............... 23 passed
tests/functional/test_task_execution_registry.py ......... 16 passed
tests/functional/test_executor_registry_status_r44.py .... 16 passed
──────────────────────────────────────────────────────────────────
Total: 55 passed
```

## 6. 改动文件

| 文件 | 改动 |
|------|------|
| `src/ops_hub/sop/executor_runner.py` | **新增** — 完整 executor runner |
| `scripts/run_live_service.py` | +16 lines — import + hook + try/catch |
| `sop-data-hub/src/ops_hub/sop/executor_runner.py` | 同步副本 |

## 7. Git

- branch: codex/sop-real-sop-topology-audit-20260525
- commit: [this commit]
- PR: 无
