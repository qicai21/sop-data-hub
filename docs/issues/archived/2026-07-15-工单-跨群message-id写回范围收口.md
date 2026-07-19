# 工单：跨群 message_id 写回范围收口

- 日期：2026-07-15
- 状态：已完成
- 优先级：P1

## 现象

`message_inbox.message_id` 在多个微信群中可重复（实测 344 个重复值）。部分写回只以 `message_id` 为条件，可能污染另一群同名消息。

## 目标

1. 生产写回优先使用 `message_inbox.id`。
2. 路由写回必须使用 `(group_id, message_id)`，不能跨群批量更新。
3. 无 inbox 主键时禁止执行不受范围约束的写回。
4. 固化两个群同 message_id 的回归测试。

## 验收

- 两条不同群、相同 message_id 的消息中，只更新目标行。
- 旧调用不会静默跨群写入。

## 实施结果

- 文本路由写回改为 `(group_id, message_id)`。
- workflow task 写回没有 `message_inbox_id` 时直接拒绝写回。
- 候选回填的 inbox 更新改按 `message_inbox.id`。
- 增加两个同 message_id、不同群的回归测试。
