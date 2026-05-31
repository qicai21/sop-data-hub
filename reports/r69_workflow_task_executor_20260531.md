# R69: workflow_task_db → executor_runner 统一执行入口

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R68 (`30d1ff8`)

---

## 1. 目标

用 `workflow_task_db` 驱动 executor_runner，替代 live_service 中硬编码的 `"四平"` 直通触发。

**新链路：** `message_inbox → workflow_task_db → workflow_task_executor → executor_runner → 回写 DB`

---

## 2. 修改文件

| 文件 | 操作 |
|---|---|
| `src/ops_hub/sop/workflow_task_executor.py` | **新增** — task 执行引擎 |
| `scripts/run_live_service.py` | 关闭旧 R52 直通入口，移除 import |

---

## 3. workflow_task_executor 架构

```
run_workflow_task(task_id)
  ├── jljg_departure_text_chain  → run_departure_executor_chain (dry/apply)
  ├── chaoyang_dispatch_context  → skipped/not_implemented
  ├── freight_detail_enrichment  → skipped/not_implemented
  └── generic_sop_task          → skipped/not_implemented
```

**状态流转：** `pending → running → succeeded/failed/skipped`

**回写：**
- `workflow_task_db.output_json` / `error_message` / `task_status` / `last_run_at` / `retry_count`
- `message_inbox.processing_status` → `task_succeeded/task_failed/task_skipped`

**幂等：** succeeded/failed 任务默认 skip，`--force` 可重执行。

---

## 4. wx_2206 执行结果

| 步骤 | 结果 |
|---|---|
| 1. parse_departure_text | ✅ complete: 煤六, 45车, 四平, 蓝鳍 |
| 2. query_95306 | ✅ 45 candidates, exact match 45 |
| 3. create_wagon_shipments | ✅ safe_to_apply (45 skip_existing) |
| 4-6. excel/factory/send | 跳过（apply_mode=false） |
| **task_status** | **succeeded** ✅ |
| **message_inbox status** | **task_succeeded** ✅ |

---

## 5. 验收清单

| # | 检查项 | 结果 |
|---|---|---|
| 1 | wx_2206 task 执行: succeeded | ✅ |
| 2 | output_json 含 3 步完整输出 | ✅ |
| 3 | message_inbox → task_succeeded | ✅ |
| 4 | 重复执行幂等: skipped/already succeeded | ✅ |
| 5 | chaoyang → skipped/not_implemented | ✅ |
| 6 | freight → skipped/not_implemented | ✅ |
| 7 | live_service alive=true | ✅ PID 94390 |
| 8 | cursor 9 sources 未回退 | ✅ |
| 9 | 旧直通入口已关闭 | ✅ |
| 10 | 未外部上传/未微信发送/未写 95306_collection | ✅ |

---

## 6. CLI 用法

```bash
# 执行单个任务（dry-run）
python -m ops_hub.sop.workflow_task_executor --run-task 1

# 执行全部 pending（dry-run）
python -m ops_hub.sop.workflow_task_executor --run-pending --limit 5

# 仅执行 jljg 类型
python -m ops_hub.sop.workflow_task_executor --run-pending --task-type jljg_departure_text_chain

# 真实执行（apply）
python -m ops_hub.sop.workflow_task_executor --run-task 1 --apply

# 查看任务+message_inbox
python -m ops_hub.sop.workflow_task_executor --get 1
```

---

## 7. Commit

**`5e6bb9d`**

`src/ops_hub/sop/workflow_task_executor.py` (新增) + `scripts/run_live_service.py` (关闭旧入口)
