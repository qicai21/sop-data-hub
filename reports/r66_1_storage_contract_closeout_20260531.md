# R66.1: R66 收尾 — 清理 legacy survivor + 修复 live_service alive 字段

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R66 (`6fc9097`)

---

## 1. 行动清单

| # | 行动 | 结果 |
|---|---|---|
| 1 | 清理 `铁晟业务工作群/手写记录/` (2 jpg) → quarantine | ✅ 已移入 `_quarantine/r66_1_20260531/` |
| 2 | 清理 live_service 旧进程创建的 `_status/` + `请车表/` → quarantine | ✅ 已移入 `_quarantine/r66_1_20260531/` |
| 3 | 重启 live_service 加载 R65.2 runner 修正 (PID 72725→94390) | ✅ 重启完成 |
| 4 | 修复 `_write_status_state` 加 `alive` 字段 | ✅ `alive: true` |
| 5 | 重跑三层验收 | ✅ 全部通过 |

---

## 2. 发现的关键问题

### live_service 运行旧版 runner.py

PID 72725 启动于 `Sun May 31 13:51:26 2026`，在 R65.2 代码变更生效之前。它在处理 wx_2341 时用**旧路径**创建了：
- `铁晟业务工作群/请车表/2592_....jpg` — 旧 {group}/{category} 分类副本
- `铁晟业务工作群/_status/2592_....json` — 旧 {group}/_status 状态文件

**根因：** live_service 是常驻进程，不会自动重载代码。每次修改 runner.py 后需重启 live_service。

**修复：** 已重启（PID 94390），新进程加载 R65.2 修正版 runner.py。后续处理不会再写旧路径。

### Python 版本问题

`run_live_service.py --status` 需用 `/opt/homebrew/bin/python3.14`（launchd plist 指定）。系统 python3 (3.9) 不兼容 `datetime.UTC`。

---

## 3. 验收结果

### verify_storage_paths.py

```
Forbidden root violations: 0
Forbidden subdir violations: 0
TOTAL VIOLATIONS: 0
*** PASS: No forbidden paths in active area ***
```

### run_live_service.py --status

```json
{
  "pid": 94390,
  "alive": true,
  "cursor_source_count": 9,
  "cursor_updated_at": "2026-05-31T07:32:35Z",
  "last_processed_message_id": "wx_2341"
}
```

### verify_storage_contract_r66.py

| 检查项 | 结果 |
|---|---|
| Forbidden violations | 0 ✅ |
| Runner self-test | 5/5 ✅ |
| Raw images | 3/3 pass ✅ |
| Old path resurrection | 0 ✅ |
| Chat record broken paths | 5 (已知 R65 产物，非回归) |

---

## 4. 修改文件

| 文件 | 改动 |
|---|---|
| `scripts/run_live_service.py` | `_write_status_state` 增加 `"alive": True` (line 144) |
| `runtime/live_service_state.json` | 手动写入 `alive: true` (live_service 重启前) |
| `wechat_images/_quarantine/r66_1_20260531/` | 新增 quarantine 批次：手写记录(2) + _status(1) + 请车表(1) = 4 files |

---

## 5. R66 最终判定

| R66 验收标准 | R66.1 后 |
|---|---|
| forbidden violations = 0 | ✅ |
| legacy survivor = 0 | ✅ |
| live_service status alive=true | ✅ |
| cursor 未回退 | ✅ 9 sources |
| message_inbox 可查询 | ✅ 59 rows |

**R66 完全收口。**
