# R70: External Action Idempotency Key & Execution Ledger

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R69 (`5e6bb9d`)

---

## 1. 目标

为 Excel 生成、工厂上传、微信发送等外部动作建立幂等账本 `external_action_log`，防止 replay/restart/force/retry 时重复发送或上传。

---

## 2. 表结构

**表名：** `external_action_log`（`sop_agent.db`）

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| workflow_task_id | INTEGER | 关联 workflow_task_db.id |
| message_inbox_id | INTEGER | 关联 message_inbox.id |
| message_id | TEXT | wx_2206 |
| project_id | TEXT | jilin_jingang_jinzhou |
| action_type | TEXT NOT NULL | generate_shipping_excel 等 |
| idempotency_key | TEXT NOT NULL UNIQUE | 幂等键 |
| action_status | TEXT DEFAULT 'planned' | planned/skipped_dry_run/executed/failed |
| request_json | TEXT | 请求参数 |
| response_json | TEXT | 执行结果 |
| error_message | TEXT | 错误信息 |
| artifact_path | TEXT | 产物路径 |
| target_system | TEXT | local/wechat/jilin_jingang_factory |
| target_channel | TEXT | 郭东北 |
| created_at / updated_at / executed_at | TEXT | 时间戳 |

**唯一约束：** `UNIQUE(idempotency_key)`

---

## 3. 幂等键规则

**格式：** `project_id:action_type:business_key`

| action_type | business_key |
|---|---|
| `generate_shipping_excel` | `<release_batch_id>:<wagon_count>` |
| `factory_upload_submit` | `<release_batch_id>:<wagon_count>` |
| `send_shipping_excel_wechat` | `<release_batch_id>:<wagon_count>` |

若 `release_batch_id` 缺失，fallback 使用 `fallback:<message_id>:<step>`，并标记 `fallback_key=true`。

---

## 4. wx_2206 External Actions

| id | action_type | status | idempotency_key |
|---|---|---|---|
| 1 | generate_shipping_excel | skipped_dry_run | jilin_jingang_jinzhou:generate_shipping_excel:1786033...:45 |
| 2 | factory_upload_submit | skipped_dry_run | jilin_jingang_jinzhou:factory_upload_submit:1786033...:45 |
| 3 | send_shipping_excel_wechat | skipped_dry_run | jilin_jingang_jinzhou:send_shipping_excel_wechat:1786033...:45 |

---

## 5. 验收清单

| # | 检查项 | 结果 |
|---|---|---|
| 1 | external_action_log 表创建 | ✅ |
| 2 | wx_2206 dry-run → 3 skipped_dry_run | ✅ |
| 3 | 重复执行幂等: planned=0, skipped_duplicate=3 | ✅ |
| 4 | UNIQUE(idempotency_key) 约束有效 | ✅ IntegrityError |
| 5 | output_json 含 external_actions 摘要 | ✅ |
| 6 | live_service alive=true | ✅ PID 94390 |
| 7 | cursor 9 sources 未回退 | ✅ |
| 8 | 未真实上传/微信发送 | ✅ |
| 9 | 未写 95306_collection.sqlite3 | ✅ |
| 10 | task 幂等仍有效 | ✅ |

---

## 6. 修改文件

| 文件 | 操作 |
|---|---|
| `src/ops_hub/sop/external_action_log.py` | 新增：schema/DAO/幂等键/CLI |
| `src/ops_hub/sop/workflow_task_executor.py` | 集成：plan_jljg_external_actions in _execute_jljg_departure |

---

## 7. Commit

**`e17ab0d`**
