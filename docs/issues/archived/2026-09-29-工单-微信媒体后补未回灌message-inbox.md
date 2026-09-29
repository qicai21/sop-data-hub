# 工单：微信图片后补下载后未回灌 message_inbox

- **日期**：2026-09-29
- **状态**：已完成
- **范围**：`live_service` 的 `waiting_media` 重试索引、微信图片入站分类
- **theme**：T4_消息媒体可恢复性

## 现象

微信更新窗口导致图片先以 `未下载` 写入聊天 ledger。live-service 将其记为
`waiting_media`，但当前每秒轮询一次，约五分钟内即达到 200 次重试并标记
`abandoned`。稍后 wx-ui-bridge 补到原图时，ledger 已有有效路径，
`message_inbox` 却永久停在 `waiting_media`，不会再进入分类链。

2026-09-29 的直接实例：铁晟业务工作群 `wx_2026-09_2783`、
`wx_2026-09_2784`，均为林加尼 38 节检装单页面。

## 根因

重试上限按调用次数而非等待时长计算；`live_service` 的 1 秒轮询使 200 次上限
不是媒体下载的合理等待窗口。`abandoned` 条目又被重试器直接跳过。

复核中还发现两项连带缺陷：

1. 媒体就绪后的 VLM 分类若返回 HTTP 500，重试器仍会把条目标为 `done`，丢失
   可恢复机会。
2. 常驻服务与人工 `--check-waiting-media` 同时写固定
   `waiting_media_index.json.tmp`，会产生临时文件竞争。

## 修复

1. 重试间隔改为 60 秒，避免每轮轮询重复检查同一图片。
2. 仅在连续等待 6 小时后标记 `abandoned`，不再以原始 retry 次数直接终止。
3. 分类失败保留为 `waiting`，记录错误后由常驻服务串行重试；不再标记 `done`。
4. 索引使用唯一临时文件写入，消除同名 `.tmp` 的替换竞争。
5. 为本次已下载但被旧规则过早终止的两张图片重新开放索引，并只执行内部
   `message_inbox` 回灌与分类；不运行发运、Excel、门户或微信外发流程。

## 验收

1. 功能测试证明高 retry 次数但等待不足 6 小时不会被标记 `abandoned`。
2. 同一轮连续调用不会在 60 秒内递增重试计数。
3. 分类 HTTP 500 时，索引保留 `waiting`，而非误标 `done`。
4. 本次两张图片在原图可用后从 `waiting_media` 更新为已分类状态。
5. 未产生 `wagon_shipments` / `wagon_container_shipments` 写入或外部动作。

## 结案记录

- 实现限频（60 秒）与按实际等待时长（6 小时）终止的媒体重试规则。
- VLM 分类失败保留重试状态；索引临时文件改为唯一文件名，避免与常驻服务竞争。
- 回归：`tests/functional/test_waiting_media_retry_persistence.py`，3 通过。
- 运行时：`make smoke-runtime` 通过；已重启 `live-service`。
- 本次两张延迟图片已回灌并由单线程 Qwen 正常分类为“其他业务图片”，未创建
  SOP 发运记录，也未执行外部动作。
