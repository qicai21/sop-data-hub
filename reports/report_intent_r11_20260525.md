# Report: Report Intent Resolver R11

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R11 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | 83b8f5b |
| Python 版本 | Python 3.11.15 |

## 1. 结论

R11 实现了本地 `WorkflowTask -> ReportIntent` resolver。对普通货运项目（中唐、朝阳、吉林金钢），resolver 可以返回 report template、recipient target、report type、required fields、missing fields 和 status；当任务信息不足时，会明确回填 `missing_fields`，不抛异常。

## 2. 实现内容

新增本地报告意图结构：

- `ReportIntent`
  - `template_path`
  - `recipient_target`
  - `required_fields`
  - `missing_fields`
  - `report_type`
  - `status`
  - `message_id`
  - `group_id`
  - `project_id`
  - `target_sop_node`

新增本地 resolver：

- `resolve_report_intent(task)`
  - 读取 `WorkflowTask.project_id` 和 `target_sop_node` 等字段；
  - 对中唐、朝阳、吉林金钢返回 departure_report 类型的 report intent；
  - 为每个已知项目返回 `template_path` 和 `recipient_target`；
  - 如果 `project_id`、`target_sop_node`、`watch_item` 等必需字段缺失，明确返回 `missing_fields`；
  - 未引入 runtime、delivery、数据库或真实发送。

## 3. 普通货运覆盖

已覆盖的普通货运项目：

- 中唐特钢（`zhongtang_special_steel`）
- 朝阳钢铁（`chaoyang_steel`）
- 吉林金钢（`jilin_jingang_jinzhou`）

`recipient_target` 统一落到开发期联系人 `郭东北`，只做本地解析，不做真发送。

## 4. missing_fields 行为

当 WorkflowTask 信息不足时：

- `missing_fields` 明确返回；
- `status` 置为 `incomplete` 或 `unknown_project`；
- 不会因为字段缺失而 crash。

## 5. 测试场景

### 场景 A：普通货运任务解析成功

输入：

- 中唐 / 朝阳 / 吉林金钢任务，包含 `project_id` 和 `target_sop_node`

结果：

- `status = ready`
- `report_type = departure_report`
- `template_path` 和 `recipient_target` 可解析
- `missing_fields = []`

### 场景 B：任务信息不足

输入：

- `WorkflowTask` 缺失 `target_sop_node` 或其他必需字段

结果：

- `missing_fields` 明确列出缺失项
- `status = incomplete`
- 不 crash

### 场景 C：普通货运范围限定

输入：

- 仅中唐、朝阳、吉林金钢

结果：

- 不涉及 runtime
- 不涉及 delivery
- 不涉及 DB
- 不涉及 wx-ops-agent
- 不涉及 R12

## 6. 已阅读文件清单

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/sop/workflow_task.py`
- `tests/functional/test_workflow_task_queue.py`
- `tests/fixtures/sops/zhongtang_special_steel_sop.md`
- `tests/fixtures/sops/chaoyang_steel_sop.md`
- `tests/fixtures/sops/jilin_jingang_sop.md`
- `src/ops_hub/sop/report_intent.py`
- `tests/functional/test_report_intent_resolver.py`

## 7. 测试结果

执行命令：

```bash
pytest tests/functional/test_report_intent_resolver.py -v
pytest tests/functional -v
```

结果：

- `tests/functional/test_report_intent_resolver.py` -> `2 passed`
- `tests/functional -v` -> `24 passed, 1 skipped`
- skip 仍然是 source supervision 的预期跳过测试

## 8. 明确未做

本轮没有增加：

- runtime
- 真发送
- delivery
- 数据库
- wx-ops-agent
- R12
- OCR 执行
- 资产移动/复制
- 生产流程修改

## 9. 改动文件

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/sop/report_intent.py`
- `tests/functional/test_report_intent_resolver.py`
- `reports/report_intent_r11_20260525.md`
- `reports/github_audit_report_intent_r11_20260525.md`

## 10. 下一步建议

进入 R12：`Delivery result and lifecycle closeout`。

仅在 order 明确允许后再做，不提前实现 delivery / closeout。
