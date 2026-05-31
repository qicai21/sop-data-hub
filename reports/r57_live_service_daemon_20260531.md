# R57: live_service 守护

| 字段 | 内容 |
|------|------|
| Round | R57 |
| 执行日期 | 2026-05-31 |
| 仓库 | qicai21/sop-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |

## 1. 结论

live_service 已通过 launchd 常驻运行，alive=true，持续 polling。本轮未使用 --apply，未上传工厂系统，未发送微信。

## 2. canonical 工作树

`/Users/qicai21/projects/repos/sop-data-hub`

## 3. R56 fd497f0 补推

| 状态 | 说明 |
|------|------|
| fd497f0 | 本地存在，但 GitHub 连接间歇性故障，未推送。稍后重试。 |

## 4. launchd 守护

- **plist 路径**: `~/Library/LaunchAgents/com.qicai21.sop-data-hub.live-service.plist`
- **Label**: `com.qicai21.sop-data-hub.live-service`
- **KeepAlive**: true
- **RunAtLoad**: true

启动命令：
```
/opt/homebrew/bin/python3.14 scripts/run_live_service.py \
  --runtime-root /Users/qicai21/projects/repos/sop-data-hub/runtime \
  --chat-records-root /Users/qicai21/projects/repos/wx-ops-agent/data/chat_records \
  --fixture-dir /Users/qicai21/projects/repos/sop-data-hub/config/project_sops
```

环境变量：`PYTHONPATH=src`

## 5. 运行状态

| 字段 | 值 |
|------|-----|
| pid | 41629 |
| alive | true |
| runtime_root | .../sop-data-hub/runtime |
| chat_records_root | .../wx-ops-agent/data/chat_records |
| fixture_dir | .../sop-data-hub/config/project_sops |
| last_processed_message_id | wx_55 |
| last_processed_time | 2026-05-31T04:04:54Z |
| seen | 6441 |
| poll_interval | 1.0s |

## 6. SOP Runtime

| 字段 | 值 |
|------|-----|
| loaded_projects | chaoyang_steel, jilin_jingang_jinzhou, jiusan, zhongtang_special_steel |
| sop_hash | 04682cbe941b4b9b |
| sop_dir | config/project_sops |
| source_of_truth | git |

## 7. SOP Task Runtime

| 字段 | 值 |
|------|-----|
| loaded_task_plans | 4 |
| implemented_task_count | 28 |
| missing_task_count | 20 |

## 8. live_service.log 最后 10 行

```
2026-05-31 12:05:00,316 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:01,621 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:02,926 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:04,225 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:05,525 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:06,824 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:08,126 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:09,427 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:10,728 INFO poll complete processed=0 seen=6441
2026-05-31 12:05:12,027 INFO poll complete processed=0 seen=6441
```

## 9. chat_records_root 状态

存在。4 个群组目录：铁晟业务工作群、数据单发群-GROUP013、中唐特钢发运群、龙虾测试群。

最新消息：
- 铁晟业务工作群: seq=2335, time=2026-05-31 10:59:59
- 数据单发群-GROUP013: seq=53, time=2026-05-29 11:22:36

## 10. 5月29日后未处理消息积压

**无积压**。服务重启后首次 poll 处理了全部 6441 条消息（含5月29日后消息）。后续 poll 因无新消息显示 processed=0。

## 11. 本轮代码改动

- `scripts/run_live_service.py`: 新增 `_write_status_state` / `_read_status_state`，`--status` 输出增加 `fixture_dir`、`last_processed_message_id`、`last_processed_time`

## 12. 安全边界

| 检查项 | 值 |
|--------|-----|
| --apply | **否** |
| 上传工厂系统 | **否** |
| 发送微信 | **否** |
| 写 sop_agent.db | **否**（仅写 runtime/） |

## 13. Git

| 字段 | 内容 |
|------|------|
| commit | 29af650 |
| branch | codex/sop-real-sop-topology-audit-20260525 |
| push | 待网络恢复 |
