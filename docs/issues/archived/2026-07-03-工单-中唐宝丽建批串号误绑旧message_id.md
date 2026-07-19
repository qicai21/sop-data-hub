# 工单：中唐宝丽建批串号误绑旧 message_id

- **日期**：2026-07-03
- **类型**：缺陷调查 / 待修复
- **项目**：中唐特钢

状态：已处理
## 背景

用户指出：

1. `宝丽` 的出港计划通知单已经存在，应该创建 `release_batch`
2. 同群货运补充信息也已经发出
3. 放货日期就是 `2026-07-03`，不存在“07-13 / 07-14 是通知日期”的说法

系统侧曾发送告警：

```text
⚠️ 出港计划通知单建出 0 批次,疑似漏建
船名:宝丽  通知日期:2026年07月03日
(wx_2026-07_13)可能 VL 误读日期/scope 全过滤/已存在,请人工核对
```

## 调查结论

**这次不是 VL 把日期识错了。**

实际 OCR / VLM 抽取结果正确：

- `通知日期 = 2026年07月03日`
- remark 也正确识别为 `7月3日第一次下达计划：10000吨（铁路 汐子）`
- 船名 = `宝丽`

根因在于 **message_id 串号复用**：

- `wx_2026-07_13`
- `wx_2026-07_14`

这两个 message_id 在 `2026-07-01` 的铁晟业务工作群旧消息中已经出现过一次，
`2026-07-03` 的中唐特钢发运群新消息又复用了同样编号。

结果：

- `create_release_batch task 496`
  - 实际绑到了 `2026-07-01` 那条吉林旧消息的 `input_json`
  - 但读取的 `extraction_json_path` 却指向了 `2026-07-03` 的中唐出港通知单 JSON
- `freight_detail_enrichment task 497`
  - 也出现了旧消息内容与新 summary 混杂

这导致：

- 日志/告警文案把问题误导成“日期识别异常”
- 实际是 **任务编译 / 执行阶段用 `message_id` 取消息上下文时，没有避免跨日/跨群重号**

## 当前人工修复结果

已手工补建：

- `release_batch_id = 942a5aec6b12c47cf96d12ce02f31d506ceaaeaf`
- 船名：`宝丽`
- `batch_sequence = lot01`
- `notice_date = 2026-07-03`
- `batch_date = 2026-07-03`
- `batch_quantity = 10000`
- 到站：`汐子`

已手工补齐货运信息：

- `plan_id = 90260700001`
- `contract_no = ZLZT-2026070101`
- `cargo_product_name = 麦克粉`
- `dispatch_status = enriched`

## 待修方向

1. `workflow_task` / `message_inbox` 关联时，不能只凭 `message_id`
2. 至少要收紧为：
   - `message_inbox_id` 为主键
   - 或 `group_id + message_id + received_datetime/source_file` 联合约束
3. `0 批次` 告警文案应区分：
   - 真正的 OCR/VL 日期识别异常
   - scope 全过滤
   - 已存在
   - message 上下文串号/误绑

## 验收标准

- 同月编号复用的消息，不再把旧消息 `input_json` 绑到新任务上
- `create_release_batch` / `freight_detail_enrichment` 能稳定对应正确 `message_inbox_id`
- 遇到串号时，告警文案不再误导为“VL 误读日期”
