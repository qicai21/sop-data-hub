# 工单：非 SOP 检装车误挂 pending 与 _pending 路径回写错误

创建日期：2026-07-04
项目：sop-data-hub
状态：已处理

## 背景

`wx_2026-07_320` 检装车图片识别为：

- 道线：煤四
- 日期：2026年7月3日
- 收货人/船名/到站信息：乌兰浩特铁、沈阳盛京颐昇、春日莲花
- 装车：51 节
- 排车：1 节

其中 `乌兰浩特铁` 是“流向 + 货物”的业务写法，类似 `汐子铁`、`四平铁`；它不是项目黑名单词。该业务不属于当前受管 SOP 项目，是因为 `乌兰浩特 + 铁` 没有命中当前受管项目的发运规则 / 放货批次流向白名单，不应进入人工 `pending_review`。

## 问题

1. 系统已识别为检装车通知单，但没有最终判断“整张单据明确为非 SOP 项目”，导致落入 `no_open_release_batch_or_inference -> pending_review` 兜底。
2. 图片实际保存在 `_pending/2026-07/images/464_82998b7c4009a86081e3ec7cdaf7dcee.jpg`，但候选表和 `message_inbox` 中仍保存了不存在的群目录路径：
   `/Users/qicai21/Documents/bussiness-artifacts/wechat_images/铁晟业务工作群/2026-07/464_82998b7c4009a86081e3ec7cdaf7dcee.jpg`

## 期望

1. 明确非 SOP 的检装车单直接忽略，不进入 `pending_review`。
2. 候选回写图片路径时优先使用真实存在的路径；若图片仍在 `_pending`，应写回 `_pending` 下的实际图片路径，便于人工查看。
3. 对当前误挂候选做一次数据修正。

## 处理结果

代码修复：

- `BusinessDataAgent.ingest_inspection_payload` 增加未受管流向忽略闸：没有匹配到有效发运规则，且抽取文本出现可识别的“站名 + 货物”流向，但该流向没有命中当前受管项目的 `release_dispatch_match_rules` / 开放 `release_batches` 白名单时，返回 `ignored_unmanaged_inspection_flow`，不创建候选。
- 2026-07-04 追认修正：原实现短暂使用 `乌兰浩特铁` / `乌兰浩特` 等黑名单 token 判断，这会误解业务字段含义；现已改为白名单流向判断，`汐子铁`、`汐子铁矿`、`四平铁` 等已受管流向不会被误丢弃。
- 候选路径回写改为优先使用真实存在的图片路径；若群目录路径不存在但 `extraction_json_path` 指向 `_pending/.../json/*_result.json`，则反推并回写 `_pending/.../images/*.jpg`。

数据修正：

- 候选 `d073d258530c52240b3d571f3a85c4c589099755` 已改为：
  - `status=ignored`
  - `candidate_status=ignored_non_sop`
  - `reason=ignored_unmanaged_inspection_flow`
  - `source_image_path=/Users/qicai21/Documents/bussiness-artifacts/wechat_images/_pending/2026-07/images/464_82998b7c4009a86081e3ec7cdaf7dcee.jpg`
- `message_inbox.wx_2026-07_320` 已改为：
  - `processing_status=ignored`
  - `summary=[ignored_unmanaged_inspection_flow] 乌兰浩特铁/春日莲花检装车未命中当前受管流向`
  - `raw_standard_image_path` / `msg_path` / `raw_msg_path` 指向实际 `_pending` 图片路径。

验证：

- `PYTHONPATH=src python3 -m pytest tests/test_data_agent.py -q`
- 新增用例覆盖：
  - 未受管流向检装车不建候选；
  - 已受管流向即便出现代理/收货人文本，也不被误丢弃；
  - `_pending` JSON 反推真实图片路径；
  - 原有 unmatched split group 丢弃逻辑仍通过。
- 当前 `inspection_ingestion_candidates` 中已无 `pending_review` 候选。
