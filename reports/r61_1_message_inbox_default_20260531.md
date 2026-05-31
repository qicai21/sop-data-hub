# R61.1: live_service 默认写入 message_inbox

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `fa72e2e`
**仓库:** `qicai21/sop-data-hub`

---

## 改动

`scripts/run_live_service.py` — 三处默认值从 `False` 翻转为 `True`：

| 函数 | 旧默认 | 新默认 |
|------|--------|--------|
| `process_event_once` | `write_message_inbox=False` | `write_message_inbox=True` |
| `run_once` | `write_message_inbox=False` | `write_message_inbox=True` |
| `run_live_service` | `write_message_inbox=False` | `write_message_inbox=True` |

新增 opt-out flag：`--no-write-message-inbox`

## 验收

| # | 项目 | 结果 |
|---|------|------|
| 1 | 默认启动后 replay-one wx_2206 自动写 message_inbox | ✅ id=5, media=not_required, proc=ready |
| 2 | waiting_media (wx_14) 仍写入 message_inbox | ✅ media=waiting_media, reg=pending_download |
| 3 | `--no-write-message-inbox` opt-out 有效 | ✅ row count 不变 (2→2) |
| 4 | cursor 未回退 | ✅ 8 sources, latest=2337 |
| 5 | 不触发外部上传/发送 | ✅ |
| 6 | live_service alive | ✅ pid=68787 |

## commit sha

```
fa72e2e
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。
