# Report: Message Lifecycle Matcher R8

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R8 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | 698424e |

## 1. 结论

R8 实现了一个纯本地的 `MessageEvent -> monitoring plan matcher`，只覆盖中唐特钢、朝阳钢铁、吉林金钢 / 吉林金刚相关的普通货运消息匹配。

它使用 R6/R7 已生成并接受的 `wechat_monitoring_plan` 作为输入，按 group_id 先过滤，再按 message/document 文本匹配 watch item，返回 candidate projects 与 target SOP node 映射。

## 2. 实现内容

新增的最小数据概念：

- `MessageEvent`
- `MonitoringMatch`
- `MessageMatchResult`

新增的 matcher 行为：

- 先按 `group_id` 查找对应 group plan；
- 对 image/document 类型消息，匹配 `document_type`；
- 对 text 类型消息，匹配 `message_type` 或 `text_patterns`；
- 命中后返回：
  - group_id
  - watch_item
  - candidate_projects
  - target_sop_nodes
  - reason
- 未命中时返回空 matches 和明确 reason。

## 3. 三个测试场景

### 场景 A：GROUP001 / 出港计划通知单

输入：

- `group_id=GROUP001`
- `message_type=image`
- `text=出港计划通知单`

结果：

- 命中 watch item：`出港计划通知单`
- candidate projects：
  - `zhongtang_special_steel`
  - `chaoyang_steel`
  - `jilin_jingang_jinzhou`

### 场景 B：GROUP001 / 检装车通知单

输入：

- `group_id=GROUP001`
- `message_type=image`
- `text=检装车通知单`

结果：

- 命中 watch item：`检装车通知单`
- candidate projects：
  - `zhongtang_special_steel`
  - `chaoyang_steel`
- 不包含 `jilin_jingang_jinzhou`

### 场景 C：GROUP999 / 出港计划通知单

输入：

- `group_id=GROUP999`
- `message_type=text`
- `text=出港计划通知单`

结果：

- no match
- reason：`no monitoring plan for group_id GROUP999`

## 4. 明确未实现内容

本轮没有增加：

- runtime
- wx-ops-agent 接入
- 数据库
- 95306
- 报告发送
- asset copy/move
- OCR
- 业务推断扩展
- 九三驱动设计

## 5. 测试结果

执行命令：

```bash
pytest tests/functional/test_monitoring_plan_matcher.py -v
pytest tests/functional -v
```

结果：

- `pytest tests/functional/test_monitoring_plan_matcher.py -v` -> 3 passed
- `pytest tests/functional -v` -> 16 passed, 1 skipped

## 6. 改动文件

- `src/ops_hub/sop/monitoring_plan_matcher.py`
- `tests/functional/test_monitoring_plan_matcher.py`
- `reports/message_lifecycle_matcher_r8_20260525.md`
- `reports/github_audit_message_lifecycle_matcher_r8_20260525.md`

## 7. 下一步建议

R9 应该进入 raw asset bundle registration protocol：

- `MessageEvent`
- `RawAssetBundle`
- 原始 image / OCR / json / metadata 路径绑定

但仍然保持 local functional-test based，不进入 runtime。
