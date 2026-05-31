# R68: workflow_task 表 — matched_sop 消息转可执行任务

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R67 (`a37f023`)

---

## 1. 表结构

**表名：** `workflow_task_db`（`sop_agent.db`）

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| message_inbox_id | INTEGER NOT NULL | 关联 message_inbox.id |
| message_id | TEXT NOT NULL | wx_2206 |
| project_id | TEXT NOT NULL | jilin_jingang_jinzhou |
| flow_name | TEXT NOT NULL | departure_flow |
| node_name | TEXT NOT NULL | detect_departure_message |
| task_type | TEXT NOT NULL | jljg_departure_text_chain |
| task_status | TEXT DEFAULT 'pending' | pending / running / done / failed |
| input_json | TEXT | 业务输入 JSON |
| output_json | TEXT | 执行输出 JSON |
| error_message | TEXT | 错误信息 |
| retry_count | INTEGER | 重试次数 |
| created_at / updated_at / last_run_at | TEXT | 时间戳 |

**唯一约束：** `UNIQUE(message_inbox_id, task_type)` — 防止重复生成

**索引：** project_id, flow_name, task_status, message_id, created_at

---

## 2. task_type 映射

| 条件 | task_type |
|---|---|
| jilin_jingang + departure_flow + detect_departure_message | `jljg_departure_text_chain` |
| chaoyang_steel + dispatch_flow | `chaoyang_dispatch_context` |
| flow = freight_detail_flow | `freight_detail_enrichment` |
| 其他 matched_sop | `generic_sop_task` |

---

## 3. 批量生成结果

| task_type | count | status |
|---|---|---|
| `jljg_departure_text_chain` | 1 | pending |
| `chaoyang_dispatch_context` | 2 | pending |
| `freight_detail_enrichment` | 9 | pending |
| **总计** | **12** | |

---

## 4. wx_2206 验收

```
id: 1
task_type: jljg_departure_text_chain
task_status: pending
input:
  message_id: wx_2206
  group_name: 铁晟业务工作群
  text_content: 煤六 45节 四平铁 蓝鳍
  sop_project_id: jilin_jingang_jinzhou
  sop_flow: departure_flow
  sop_node: detect_departure_message
```

✅

---

## 5. 验证清单

| # | 检查项 | 结果 |
|---|---|---|
| 1 | workflow_task_db 表创建 | ✅ |
| 2 | wx_2206 生成任务: jljg_departure_text_chain / pending | ✅ |
| 3 | 幂等：重复生成 → created=0, skipped=12 | ✅ |
| 4 | 12 matched_sop 全部生成任务 | ✅ |
| 5 | ignored (wx_2339) 不生成任务 | ✅ |
| 6 | message_inbox 未被破坏 | ✅ 12 matched_sop intact |
| 7 | live_service alive | ✅ PID 94390 |
| 8 | cursor 未回退 | ✅ 9 sources |
| 9 | 未执行 executor_runner | ✅ |
| 10 | 未上传/未发微信/未写 95306_collection | ✅ |

---

## 6. CLI 用法

```bash
# 建表
python -m ops_hub.sop.workflow_task --init-db

# 批量生成（幂等）
python -m ops_hub.sop.workflow_task --create-for-matched

# 查看 wx_2206 任务
python -m ops_hub.sop.workflow_task --get-message wx_2206

# 列出全部 pending 任务
python -m ops_hub.sop.workflow_task --list --status pending --limit 20
```

---

## 7. 修改文件

| 文件 | 操作 |
|---|---|
| `src/ops_hub/sop/workflow_task.py` | 合并：保留旧 WorkflowTask/WorkflowTaskQueue/build_workflow_task_queue + 新增 R68 DB 层 |

---

## 8. Commit

`src/ops_hub/sop/workflow_task.py` (modified: merged old + R68 DB layer)
