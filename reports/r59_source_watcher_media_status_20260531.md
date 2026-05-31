# R59: source_watcher 消息源过滤与图片媒体状态修复

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `2cd7ea0`
**仓库:** `qicai21/sop-data-hub`

---

## 1. 修改文件

| 文件 | 改动 |
|------|------|
| `src/ops_hub/sop/source_watcher.py` | 新增 `_classify_source_file_type`, `_is_chat_record_source`; 过滤 `_iter_payloads`; 修复 image media 状态 |
| `scripts/run_live_service.py` | 新增 `waiting_media` 提早短路（skip OCR/VLM/executor） |

## 2. source_file_type 分类规则

```
chat_record          — 正式聊天记录 JSONL（群目录/年月.jsonl）
image_download_todo  — 群目录/image_download_todos/*.jsonl
video_download_todo  — 群目录/video_download_todos/*.jsonl
runtime_log          — runtime/, runtime-logs/
archive              — archive/, archives/
temp                 — temp/, tmp/, _status/, _previews/, extractions/
unknown              — chat_records_root 外的路径
```

只有 `chat_record` 类型的 JSONL 会生成 `MessageEvent`。

## 3. jsonl 来源统计

| source_file_type | count |
|-----------------|-------|
| chat_record | 8 |
| image_download_todo | 8 |
| video_download_todo | 2 |
| **total** | **18** |

过滤后 chat_record events: 5124（原 6441）

## 4. R58 cursor_source_count

**18**（含 image_download_todos、video_download_todos）

## 5. R59 cursor_source_count

**8**（仅 chat_record sources）

## 6. image_download_todos 是否排除

**是。** 以下 8 个 image_download_todos JSONL 不再生成 MessageEvent：

- 中唐特钢发运群/image_download_todos/2026-04.jsonl
- 中唐特钢发运群/image_download_todos/2026-05.jsonl
- 数据单发群-GROUP013/image_download_todos/2026-05.jsonl
- 铁晟业务工作群/image_download_todos/2024-04.jsonl
- 铁晟业务工作群/image_download_todos/2026-03.jsonl
- 铁晟业务工作群/image_download_todos/2026-04.jsonl
- 铁晟业务工作群/image_download_todos/2026-05.jsonl
- 龙虾测试群/image_download_todos/2026-05.jsonl

video_download_todos（2 个）同理排除。

## 7. msg-path=未下载 样例验证

**验证消息: `wx_14`**

| 字段 | 值 |
|------|---|
| message_id | wx_14 |
| media_status | **waiting_media** ✅ |
| processing_status | **waiting_media** ✅ |
| registration_status | **pending_download** ✅ |
| msg_path | 未下载 |
| raw_image_path | None ✅ |

## 8. 正常图片样例验证

**验证消息: `wx_2`**

| 字段 | 值 |
|------|---|
| message_id | wx_2 |
| media_status | **ready** ✅ |
| processing_status | **ready** ✅ |
| registration_status | **complete** ✅ |
| raw_image_path | `/Users/qicai21/.../1121_d46c714de9407cb9b63c68b497c95161.jpg`（文件存在） ✅ |

## 9. live_service alive 状态

✅ alive=true, pid=59402

## 10. replay-one wx_2206 验证

✅ 成功：`processed=1`, `executor_runner status=complete`。Cursor 未回退，仍保持在 `wx_2337`。

## 11. 是否使用 --apply

**否。**

## 12. 是否外部上传/发送

**否。**

## 13. commit sha

```
2cd7ea0
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。

---

## 验证清单

| # | 项目 | 结果 |
|---|------|------|
| 1 | jsonl 分类统计 | chat_record=8, image_download_todo=8, video_download_todo=2 ✅ |
| 2 | image_download_todos 不再生成 MessageEvent | ✅ |
| 3 | 未下载图片: pending_download, waiting_media, None | ✅ |
| 4 | 正常图片: complete, ready, 路径存在 | ✅ |
| 5 | 重启后无历史全量重扫 (processed=0) | ✅ |
| 6 | cursor_source_count=8（不含 todo sources） | ✅ |
| 7 | replay-one wx_2206 成功 | ✅ |
| 8 | cursor 未回退 | ✅ |

## 遗留

- waiting_media 消息未被 OCR/VLM 处理（符合设计）
- 下次图片下载完成后需重新触发处理（R60+）
- source_watcher 过滤对 `snapshot()` 计数的影响可通过增加 filtered_file_count 进一步改进（非本轮必需）
