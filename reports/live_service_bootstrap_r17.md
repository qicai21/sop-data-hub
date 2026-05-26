# Report: Live Service Bootstrap R17

| 字段 | 内容 |
|------|------|
| Order ID | R17 |
| 执行日期 | 2026-05-26 |
| 执行者 | local Hermes |

## 1. 结论

已把现有本地链路串成可持续监听的 live service：`WxOpsSourceWatcher` 持续扫描 `data/chat_records/**/*.jsonl`，将新增消息依次推进到 `MessageEvent -> RawAssetBundle -> Matcher -> WorkflowTask -> DashboardPayloadQueue -> DashboardState`，并把结果写到 `runtime/events/`、`runtime/dashboard_intents/`、`runtime/dashboard_state/`。

## 2. 启动方式

- 启动命令：`python scripts/run_live_service.py`
- 当前以后台进程方式运行，持续轮询新增消息
- 启动脚本支持 `--once` 便于 bootstrap/测试

## 3. 监听目录

- 输入监听：`data/chat_records/**/*.jsonl`
- 读取 fixture：`tests/fixtures/sops/`

## 4. 日志位置

- `runtime/live_service.log`
- 终端 stdout 同步输出

## 5. 输出位置

- `runtime/events/`
- `runtime/dashboard_intents/`
- `runtime/dashboard_state/`

## 6. 测试方式

已验证：

- `pytest tests/functional/test_live_service_bootstrap.py -v` -> passed
- `pytest tests/functional -v` -> 32 passed

单次 bootstrap 验证方式：

- `python scripts/run_live_service.py --once --chat-records-root <tmp>/chat_records --runtime-root <tmp>/runtime --poll-interval 0`

## 7. 改动文件

- `scripts/run_live_service.py`
- `tests/functional/test_live_service_bootstrap.py`
- `reports/live_service_bootstrap_r17.md`

## 8. 当前运行状态

- live service 已启动并保持监听
- 等待真实微信群新增消息后刷新 `runtime/events/`、`runtime/dashboard_intents/`、`runtime/dashboard_state/`

## 9. 未修改范围

- 未修改 `wx-ops-agent`
- 未修改数据库 schema
- 未修改九三逻辑
- 未发送真实报告
- 未新增业务中间对象
