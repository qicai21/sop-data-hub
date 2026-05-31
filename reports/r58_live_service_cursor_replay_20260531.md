# R58: live_service 持久游标与回放控制

| 字段 | 内容 |
|------|------|
| Round | R58 |
| 执行日期 | 2026-05-31 |
| 仓库 | qicai21/sop-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

持久游标已实现。重启后不再全量重扫 6441 条历史消息。replay-one 单条回放可用，不破坏游标。

## 2. cursor 文件

**路径**: `runtime/cursors/live_service_cursor.json`

**样例**:
```json
{
  "version": 1,
  "bootstrap_cursor": true,
  "updated_at": "2026-05-31T04:31:04Z",
  "sources": {
    "/Users/.../铁晟业务工作群/2026-05.jsonl": {
      "last_local_id": 2337,
      "last_message_id": "wx_2337",
      "last_processed_at": "...",
      "group_name": "铁晟业务工作群"
    }
  }
}
```

- 18 个 source（含 image_download_todos 等子目录）
- 最大 local_id: 2337（铁晟业务工作群）

## 3. 默认启动行为

| 场景 | 行为 |
|------|------|
| cursor 存在 | 读取游标，跳过 local_id <= last_local_id 的消息 |
| cursor 不存在 | bootstrap：扫描全部消息，cursor 定位到最新，不处理历史 |
| `--ignore-cursor` | 忽略游标，全量扫描（仅供手动使用） |
| `--reset-cursor-to-latest` | 重建游标到最新位置，然后退出 |

验证结果：重启后 `processed=0 seen=0`，无历史重扫。

## 4. 回放参数

| 参数 | 状态 | 说明 |
|------|------|------|
| `--replay-one wx_2206` | ✅ 已实现 | 单条回放，不修改 cursor |
| `--replay-from-id N` | ✅ 已实现 | 从 local_id >= N 开始回放 |
| `--ignore-cursor` | ✅ 已实现 | 忽略游标全量扫描 |
| `--reset-cursor-to-latest` | ✅ 已实现 | 重置游标到最新 |
| `--cursor-path` | ✅ 已实现 | 自定义游标路径 |

## 5. replay-one wx_2206 验证

```
$ python scripts/run_live_service.py --replay-one wx_2206 --once
INFO processed event message_id=wx_2206 group_id=铁晟业务工作群 payloads=1 states=1
INFO replay-one message_id=wx_2206 processed
INFO poll complete processed=1 seen=0
```

结果：
- wx_2206 在 铁晟业务工作群/2026-05.jsonl 中找到
- 成功处理（payloads=1 states=1）
- cursor 未被修改（铁晟 2026-05 保持 last_local_id=2337）

## 6. status 输出

```
cursor_path: .../runtime/cursors/live_service_cursor.json
cursor_exists: True
cursor_source_count: 18
cursor_latest_message_id: wx_2337
cursor_latest_local_id: 2337
cursor_updated_at: 2026-05-31T04:31:04Z
cursor_bootstrap: True
```

## 7. launchd 当前命令

```
/opt/homebrew/bin/python3.14 scripts/run_live_service.py
  --runtime-root .../sop-data-hub/runtime
  --chat-records-root .../wx-ops-agent/data/chat_records
  --fixture-dir .../sop-data-hub/config/project_sops
```

无 `--ignore-cursor`、`--replay-one`、`--replay-from-id`、`--apply`。

## 8. 安全边界

| 检查项 | 值 |
|--------|-----|
| --apply | 否 |
| 上传工厂系统 | 否 |
| 发送微信 | 否 |

## 9. 代码改动

- `scripts/run_live_service.py`：
  - 新增 `_cursor_path`、`_load_cursor`、`_save_cursor`（原子 tmp→rename）、`_bootstrap_cursor_to_latest`、`_update_cursor_for_event`
  - `run_once`：增加 cursor 过滤 + replay_one/replay_from_id 逻辑
  - `run_live_service`：增加 cursor 初始化、新参数传递、transient mode PID 跳过
  - `status_command`：增加 8 个 cursor_* 字段
  - CLI args：6 个新参数

## 10. Git

| 字段 | 内容 |
|------|------|
| commit | （待提交） |
