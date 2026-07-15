# 工单：waiting_media 重试持久化与外部动作状态账

- 日期：2026-07-15
- 状态：已完成
- 优先级：P1

## 现象

1. `waiting_media` 没有成功重试时，递增后的 `retries` 没有写回索引，重启后会从旧次数继续。
2. 吉林发运链的外部动作在链路失败时仍保留 `planned`，且成功标记没有逐步骤核验真实结果。

## 目标

1. 所有重试状态变化均持久化。
2. 外部动作按 Excel、收货人上传、微信发送逐项标记 executed/failed；不把失败步骤标成 executed。
3. 不批量改写历史 `planned` 记录，先保留审计证据。
4. 加入无媒体重试与步骤失败的回归测试。

## 验收

- 无可用媒体时 retries 与 last_checked_at 可在索引文件中读回。
- 某一步失败时对应 action 为 failed，未发生的后续动作不被标为 executed。

## 实施结果

- `waiting_media` 在无 payload、仍等待、到达最大次数、完成等任意状态变化后均写回索引。
- 吉林链的 Excel、门户上传、微信发送分别按实际结果记为 `executed` 或 `failed`。
- 历史 `planned` 记录未批量改写，保留给后续审计核实。
- 修复 `run_live_service.py --status` 的自举导入，使健康检查不依赖外部 `PYTHONPATH`。
