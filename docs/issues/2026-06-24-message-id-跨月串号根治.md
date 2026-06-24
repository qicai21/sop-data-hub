# message_id 跨月串号 —— 共同根因根治

- **提出日期**:2026-06-24(wx-ops-agent 加固时多次撞到、铁证确凿)
- **类型**:根因 / 数据一致性
- **优先级**:P1(本周反复咬人,且会持续生幽灵/丢图)

## 现象(本 session 实证,全是同一根因)
1. **检装单图不落候选**:wx_1637 / wx_1663 图片卡 waiting_media、不分类、业务链路断。
2. **幽灵触发器**:#387 宝腾海、#391/#392 串号生出的假触发器,要人工逐个消除。
3. **retry 永久 abandoned**:`waiting_media_index` 1927 条里 **1600 abandoned**,绝大多数因 message_id 串号读错 source_file → 无脑 retry 永不成功(`run_live_service.py:431` 注释自承)。
4. **铁证**:index 里 `铁晟业务工作群:2026-04:1637`(abandoned)—— wx_1637 本是 **2026-06** 的检装单,却被钉成 **2026-04** 的 key → 读错月文件 → 永久放弃。

## 根因
message_id = `wx_{local_id}`,而 `local_id` 取自 ledger 的 `seq`(见 `source_watcher._build_event`:`local_id = payload.get("local_id") or payload.get("seq")`)。
**seq 在 wx-ops-agent 是逐群逐月各自从 1 自增**(`chat_records/store.py:_next_seq` 按月文件算)→ **跨月同 seq 必然重号** → `wx_{seq}` 跨月串号。
下游 message_inbox 唯一键虽已加 `source_file` 第 4 维(#118)缓解了 inbox 去重,但:
- `waiting_media_index` 的 key / retry 仍按 `(local_id, source_file)`,跨月撞名仍读错文件;
- `wx_{seq}` 作为对外 message_id 在 workflow_task / 触发器层到处用,串号四处传染。

## 待办(需定方案)
- **方案 A(推荐)message_id 加时间维度**:`wx_{YYYYMM}_{seq}` 或 `wx_{local_id}_{create_time}`,让其全局唯一、跨月不撞。改 `source_watcher._build_event` 的 message_id 生成 + 兼容存量(映射 / 双读)。
- **方案 B**:wx-ops-agent 侧 seq 改全局单调(不逐月重置)——动 ledger 写端,影响面更大。
- **守门**:跨月同 seq 两条消息 → message_id 不同 + waiting_media_index key 不撞 + retry 读对文件 的回归测试。
- **清理**:存量 1600 abandoned 中真实有效的(若有)按新 key 重建;幽灵的丢弃。

## 关联
本工单是 [[2026-06-24-wx-ops-agent-hardening]] 收口时定位的共同根因;另与历史 `sop_pipeline_bugs_2026_06_07`(跨月 message_id 冲突)同源。
