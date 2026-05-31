# R59.1: waiting_media 图片补下载后的重新触发

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `a5a358b`
**仓库:** `qicai21/sop-data-hub`

---

## 1. 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/run_live_service.py` | 新增 waiting_media_index 机制、retry 逻辑、`--check-waiting-media` 命令、status 增强 |

## 2. waiting_media_index 设计

**文件路径:** `runtime/waiting_media_index.json`

**结构:**
```json
{
  "version": 1,
  "items": {
    "wx_14": {
      "message_id": "wx_14",
      "source_file": "/path/to/chat_records/群名/2026-04.jsonl",
      "local_id": 14,
      "group_name": "中唐特钢发运群",
      "image_md5": "",
      "msg_path": "未下载",
      "media_status": "waiting_media",
      "added_at": "2026-05-31T05:05:17Z",
      "retries": 0,
      "status": "waiting",
      "last_checked_at": "..."
    }
  }
}
```

完成后状态变为 `"status": "done"` + `"resolved_at"`。

## 3. 核心机制

### 3.1 写入（polling 时）

`process_event_once` 遇到 `processing_status == "waiting_media"` 时：
- 调用 `_add_to_waiting_media_index(runtime_root, event, logger)`
- 原子写入 `.tmp` → `rename`

### 3.2 重试（polling 时）

`run_once` 在正常处理循环后调用 `_retry_waiting_media()`：
- 遍历 index 中 `status != "done"` 的条目
- 从原始 JSONL 重读 payload
- 通过 `source_watcher._build_event()` 重新评估 media 状态
- 若 `processing_status != "waiting_media"` → 处理之 → 标记 done
- cursor=None，**不回退 cursor**

### 3.3 独立命令

```bash
python scripts/run_live_service.py --check-waiting-media
```

按需检查 index 并重试已就绪的 waiting_media，输出 JSON 结果。

### 3.4 status 输出

`--status` 新增 `waiting_media_index` 段：
```json
{
  "waiting_media_index": {
    "path": ".../runtime/waiting_media_index.json",
    "exists": true,
    "total": 1,
    "waiting": 0,
    "done": 1
  }
}
```

## 4. 验证结果

### 4.1 wx_14 进入 waiting_media_index

```
$ python scripts/run_live_service.py --replay-one wx_14 --once
→ waiting_media_index: added message_id=wx_14
→ waiting_media message_id=wx_14 media_status=waiting_media registration=pending_download
```

✅ Index 写入成功，msg_path=未下载，media_status=waiting_media。

### 4.2 模拟图片补齐后重新处理

1. 创建测试 JSONL（msg-path 指向真实图片文件 `/tmp/r591_wm_test/real_image_test.jpg`）
2. 更新 index 的 source_file 指向测试 JSONL
3. 运行 `--check-waiting-media`:

```
waiting_media_retry: media ready message_id=wx_14 media_status=waiting_media -> ready
processed event message_id=wx_14 ... payloads=0 states=0
waiting_media_index: marked done message_id=wx_14
{"action": "check_waiting_media", "retried": 1, "pending_before": 1, "applied": false}
```

✅ 重新处理成功。

### 4.3 cursor 未回退

```
中唐特钢发运群/2026-04: last_local_id=49  （不是 14）
cursor_source_count: 8
```

✅ Cursor 保持原值不变。

### 4.4 幂等性

再次运行 `--check-waiting-media`：

```
pending=0, retried=0
```

✅ 已完成的条目不会被重复处理。

### 4.5 live_service alive

```
alive: True, pid: 62295
poll complete processed=0 seen=0
```

✅ 无历史重扫。

### 4.6 不全量重扫历史

✅ `processed=0` — cursor 生效，无历史重扫。

## 5. 是否使用 --apply

**否。** 全程未使用 `--apply`。

## 6. 是否外部上传/发送

**否。**

## 7. commit sha

```
a5a358b
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。

---

## 遗留

- `_retry_waiting_media` 仅在 `run_once` 末尾调用，不在 `replay-one` 路径调用（符合设计）
- waiting_media_index 中的条目如果 JSONL payload 永久不可用（源文件被删除），条目不会自动清理。可后续加 max_retries 阈值自动标记 done
