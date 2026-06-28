# 工单:检装车候选创建后 message_inbox.inspection_candidate_id 未回填 → verifier 永远 no_inbox_link、候选卡死 pending_95306_match

- **类型**:代码缺陷(回链缺失 → 重试链路断)
- **发现日期**:2026-06-28
- **发现会话**:数据运维(只改数据,不改代码)
- **处理归属**:系统开发会话
- **严重度**:高 —— 候选永久卡 pending,即使 95306 制票早已齐,verifier 也永不前进,需人工才能入库

## 现象

朝钢 马兰幸福 55车 检装车候选(`e0d89227…`,源图 497 / message wx_2026-06_2135 / message_inbox 100002190)10:00 建,挂 `pending_95306_match`。95306 制票 10:36 就全出了(55车朝阳西,55/55 命中),但 12:40+ 过去 2h+ 仍未入库。

verifier 日志每轮 `scanned:1 retried:1 succeeded:0 still_pending:1`,看似在重试,实则没真正跑链。

## 根因

`pending_match_verifier._retry_chain()` 先调 `_find_inbox_id(candidate_id)`:
```
SELECT id, message_id FROM message_inbox WHERE inspection_candidate_id = ? ...
```
但**创建该候选时,对应 message_inbox 行(100002190)的 `inspection_candidate_id` 没被回填**(为 NULL)。于是:
- `_find_inbox_id` 返回 `(None, '')`
- `_retry_chain` 直接返回 `{"status":"no_inbox_link"}`,**根本不调 `_execute_chaoyang_inspection_chain`**
- verifier 记为 retried 但 succeeded=0 → 候选永远 still_pending,**与制票出没出无关**。

实证:制票齐后手动跑 `recover_loading_cars_via_window` 直接 `status=ok / 窗内55 / 真装55`(链本身没问题),纯卡在回链缺失。

候选与 inbox 其实有 `message_id`(候选.message_id = wx_2026-06_2135 = inbox.message_id)能对上,但 `_find_inbox_id` 只认 `inspection_candidate_id` 这一列。

## 期望 / 建议

- **创建检装车候选时,必须同时回填 `message_inbox.inspection_candidate_id`**(创建与回链放同一事务)。
- 或 `_find_inbox_id` 增加兜底:`inspection_candidate_id` 查不到时,用候选的 `message_id` 反查 message_inbox。
- verifier 把 `no_inbox_link` 单独计数并告警(现在被淹没在 still_pending 里,看不出是断链)。
- 排查:哪些候选创建路径会漏回填(本例源图在铁晟群、靠 known_ships 船名消歧建的候选,可能走了不回填 inbox 的支路)。

## 数据侧已处置

- 手动 `UPDATE message_inbox SET inspection_candidate_id='e0d89227…' WHERE id=100002190`,`_find_inbox_id` 恢复正常,verifier 下轮即可重试入库(朝钢走鞍钢上传+反查)。
- 建议扫一遍存量 `pending_95306_match` / 长期 pending 候选,核对 inbox 回链是否缺失。

关联工单 2026-06-28-共享群检装车单授权、记忆 [[inspection-ingest-wagon-shipments-authority]]、[[chaoyang-ansteel-upload-verify-rule]]。
