# R58: live_service 持久游标与回放控制

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `ccb4a2d`
**仓库:** `qicai21/sop-data-hub`

---

## 1. cursor 文件路径

```
/Users/qicai21/projects/repos/sop-data-hub/runtime/cursors/live_service_cursor.json
```

## 2. cursor JSON 样例

```json
{
  "version": 1,
  "bootstrap_cursor": true,
  "sources": {
    "<source_file_path>": {
      "last_local_id": 2337,
      "last_message_id": "wx_2337",
      "last_processed_at": "2026-05-31T04:31:04Z",
      "group_name": "铁晟业务工作群"
    }
  }
}
```

当前实际 cursor：18 个 source，latest `wx_2337`。

## 3. 默认启动是否重扫历史

**否。** 默认启动（launchd 和手动 `python scripts/run_live_service.py`）读取 cursor 后只处理 `local_id > last_local_id` 的新消息。

验证结果：
- 停止服务 → 删除 cursor → 用 `--reset-cursor-to-latest` 初始化 → 重启 launchd
- 首次 poll：`processed=0 seen=0`（无历史重扫）
- 后续 poll 持续 `processed=0 seen=0`（正常空闲）

首次无 cursor 时，自动 bootstrap 到各 source 最新消息，不处理历史。

## 4. replay-one 验证结果

```bash
$ python scripts/run_live_service.py --replay-one wx_2206 --once
```

**结果：成功。** 日志确认只处理了 `wx_2206` 一条消息。

cursor 未被 replay 修改——正常 cursor 仍保持在 `wx_2337`。

## 5. replay-from-id 是否实现

**是。** 参数 `--replay-from-id` 支持从 `local_id >= N` 开始回放。因未找到 `wx_2206` 之后的真实新消息，未单独验证区间回放，但代码路径与 `--replay-one` 共用过滤逻辑。

## 6. ignore-cursor 是否实现

**是。** `--ignore-cursor` 会跳过游标读取，按旧逻辑扫描所有消息。仅允许手动运行使用，**不写入 launchd 配置**。

## 7. reset-cursor-to-latest 是否实现

**是。** `--reset-cursor-to-latest --once` 将 cursor 重置到各 source 当前最新消息，不处理历史。实现方式：扫描所有 source → 写入 cursor 文件 → 退出。

## 8. status 输出摘要

```
cursor_path:         /Users/.../runtime/cursors/live_service_cursor.json
cursor_exists:       true
cursor_source_count: 18
cursor_latest_message_id: wx_2337
cursor_latest_local_id:  2337
cursor_updated_at:   2026-05-31T04:31:04Z
cursor_bootstrap:    true
```

另外：`pid=56058`, `alive=true`, `processed=0`, `seen=0`。

## 9. launchd 当前命令

```
/opt/homebrew/bin/python3.14
scripts/run_live_service.py
--runtime-root /Users/qicai21/projects/repos/sop-data-hub/runtime
--chat-records-root /Users/qicai21/projects/repos/wx-ops-agent/data/chat_records
--fixture-dir /Users/qicai21/projects/repos/sop-data-hub/config/project_sops
```

**不包含：** `--ignore-cursor`, `--replay-one`, `--replay-from-id`, `--apply`。

## 10. 是否使用 --apply

**否。** 本轮 live_service 保持不带 `--apply`。

## 11. 是否触发外部上传/发送

**否。** 无 `--apply`，无外部上传或微信发送。

## 12. commit sha

```
ccb4a2d
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。

---

## 改动文件

| 文件 | 改动 |
|------|------|
| `scripts/run_live_service.py` | 新增 cursor 管理、replay 参数、status 增强、PID 文件防护 |

## 遗留

- `source_watcher` 仍在扫描 `image_download_todos/` 子目录（R59 处理）
- cursor 写入已是原子操作（`.tmp` → `rename`）
- 无 `--apply` 场景下 replay-one 仅执行类目分类 + 文字路由，不会触发外部动作
