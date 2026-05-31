# R61: message_inbox 表

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `7ff8c94`
**仓库:** `qicai21/sop-data-hub`

---

## 1. 新增/修改文件

| 文件 | 改动 |
|------|------|
| `src/ops_hub/sop/message_inbox.py` | **新增** — schema, DAO, CLI (~330 行) |
| `scripts/run_live_service.py` | 新增 `--write-message-inbox` flag + 调用链 |

## 2. message_inbox 表结构

**数据库:** `data/sop_agent.db`

39 列，涵盖 storage_policy manifest.required_fields 所需的全部字段：

| 分类 | 字段 |
|------|------|
| 来源 | source_agent, channel, group_id, group_name |
| 消息标识 | message_id (NOT NULL), local_id, source_file, source_file_type, source_file_stem |
| 内容 | msg_type, received_datetime, sender, text_content |
| 媒体路径 | raw_msg_path, msg_path |
| 状态 | media_status, registration_status, processing_status (DEFAULT 'new'), missing_media_path |
| 标准图 | raw_standard_image_path |
| 分类 | document_type, classification_status, classification_label |
| 归档路径 | extraction_json_path, business_archive_image_path, business_archive_json_path, data_file_path |
| SOP 命中 | is_sop_msg (DEFAULT 0), sop_project_id, sop_flow, sop_node, summary |
| 业务关联 | release_batch_id, inspection_candidate_id |
| DB 操作 | db_action, db_record_ids, content_sha256 |
| 运维 | retry_count (DEFAULT 0), error_message, created_at, updated_at, last_seen_at |

## 3. 唯一约束选择理由

**`UNIQUE(source_agent, group_id, message_id)`**

理由：`message_id = wx_{local_id}` 在不同群之间会重复（如 `wx_2` 同时存在于 铁晟业务工作群 和 中唐特钢发运群）。加入 `group_id` 和 `source_agent` 确保不会误合并不同群/不同来源的同类消息。

## 4. 索引

| 索引 | 字段 |
|------|------|
| `idx_message_inbox_unique` | (source_agent, group_id, message_id) UNIQUE |
| `idx_message_inbox_message_id` | (message_id) |
| `idx_message_inbox_group_id` | (group_id) |
| `idx_message_inbox_processing_status` | (processing_status) |
| `idx_message_inbox_media_status` | (media_status) |
| `idx_message_inbox_sop_project` | (sop_project_id) |
| `idx_message_inbox_received_datetime` | (received_datetime) |

## 5. 状态约定

### processing_status

| 值 | 说明 |
|----|------|
| new | 新消息，未开始处理 |
| waiting_media | 媒体未就绪，等待下载 |
| ready | 媒体已就绪 |
| classified | 已分类 |
| matched_sop | 已命中 SOP |
| task_created | 已创建 workflow_task |
| processing | 处理中 |
| done | 处理完成 |
| failed | 处理失败 |
| ignored | 忽略 |

### media_status

| 值 | 说明 |
|----|------|
| not_required | 文字消息，不需要媒体 |
| waiting_media | 媒体未下载/文件缺失 |
| ready | 媒体文件可用 |
| recorded_only | 仅记录（不处理），用于 video/file |
| missing_file | 路径存在但文件缺失 |

### registration_status

| 值 | 说明 |
|----|------|
| pending_download | 待下载 |
| complete | 注册完成 |
| missing_file | 文件缺失 |
| recorded_only | 仅记录 |

## 6. DAO 函数清单

| 函数 | 说明 |
|------|------|
| `ensure_message_inbox_schema(db_path)` | 创建表和索引 |
| `message_event_to_inbox_row(event, extra)` | MessageEvent → dict |
| `upsert_message_inbox_event(event, db_path, extra)` | INSERT or UPDATE |
| `get_message_inbox_by_message_id(message_id, db_path, group_id)` | 单条查询 |
| `list_message_inbox(db_path, status, limit)` | 列表查询 |

## 7. CLI 用法

| 命令 | 说明 |
|------|------|
| `python -m ops_hub.sop.message_inbox --init-db` | 初始化表 |
| `python -m ops_hub.sop.message_inbox --ingest-message wx_2206` | 写入单条 |
| `python -m ops_hub.sop.message_inbox --self-test` | 三条消息自测 |
| `python -m ops_hub.sop.message_inbox --get wx_2206` | 查询单条 |
| `python -m ops_hub.sop.message_inbox --list --limit 10` | 列表 |
| `python -m ops_hub.sop.message_inbox --list --status waiting_media` | 按状态过滤 |

live_service 接入：

```bash
python scripts/run_live_service.py --replay-one wx_2206 --once --write-message-inbox
```

## 8. wx_2206 写入验证

```
id=1, group=铁晟业务工作群, msg_type=text
media_status=not_required, processing_status=ready, registration_status=complete
```

✅ 文字消息正确写入，media_status=not_required。

## 9. wx_2206 重复写入幂等验证

```
第一次: action=updated, id=1
第二次: action=updated, id=1 (same)
same id: True
updated_at changed: True
created_at preserved: True
```

✅ 幂等 — 无重复插入，updated_at/last_seen_at 更新。

## 10. wx_14 waiting_media 写入验证

```
id=2, group=中唐特钢发运群, msg_type=image
media_status=waiting_media, processing_status=waiting_media, registration_status=pending_download
```

✅ waiting_media 状态完整写入。

## 11. wx_2 ready image 写入验证

```
id=4, group=铁晟业务工作群, msg_type=image
media_status=ready, processing_status=ready, registration_status=complete
raw path 存在
```

✅ ready 图片状态完整写入。（注：id=3 的 wx_2 来自 中唐特钢发运群，因文件缺失被标记为 waiting_media，符合 R59 设计。）

## 12. live_service alive 状态

✅ alive=true, pid=66758

## 13. cursor 状态

✅ cursor_source_count=8, cursor_latest_local_id=2337（未回退）

## 14. 是否写 95306_collection.sqlite3

**否。** 全程只写 `sop_agent.db` 中的 `message_inbox` 表。

## 15. 是否外部上传/发送

**否。**

## 16. commit sha

```
7ff8c94
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。
