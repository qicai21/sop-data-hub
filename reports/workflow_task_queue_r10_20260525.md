# Report: Workflow Task Queue R10

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R10 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | 2c70a51 |
| Python 版本 | Python 3.11.15 |
| pip install | 未重跑；直接使用现有本地 venv |

## 1. 结论

R10 实现了本地 `WorkflowTask / TodoItem` planner。输入 `MessageEvent + RawAssetBundle + MessageMatchResult` 后：

- 命中匹配时生成 `WorkflowTask`；
- 未命中时生成 `TodoItem(category=no_match)`；
- 资产登记不完整时额外生成 `TodoItem(category=incomplete_registration)`；
- 全程不接 runtime、wx-ops-agent、数据库、95306、OCR 执行、报告发送或 asset 移动。

## 2. 实现内容

新增本地 planner：

- `WorkflowTask`
  - `task_id`
  - `message_id`
  - `group_id`
  - `project_id`
  - `target_sop_node`
  - `watch_item`
  - `raw_asset_bundle`
  - `status`
  - `reason`
- `TodoItem`
  - `todo_id`
  - `message_id`
  - `group_id`
  - `category`
  - `reason`
  - `raw_asset_bundle`
  - `suggested_action`
  - `status`
- `WorkflowTaskQueue`
  - `event`
  - `raw_asset_bundle`
  - `match_result`
  - `workflow_tasks`
  - `todo_items`
  - `reason`

规划规则：

- `MessageMatchResult.matches` 有结果时，按 `project_id / target_sop_node` 生成 task；
- `MessageMatchResult.matches` 为空时，生成 `no_match` todo；
- `RawAssetBundle.registration_status != complete` 时，生成 `incomplete_registration` todo；
- task / todo 都保留 message_id、group_id、watch_item、project_id、target SOP node、raw asset bundle 绑定。

## 3. 三个测试场景

### 场景 A：matched 出港计划通知单

输入：

- `GROUP001` image message
- `text: 出港计划通知单`
- complete `RawAssetBundle`

结果：

- 生成 ordinary freight 相关 workflow tasks；
- 每个 task 带 message id、group id、project id、target SOP node、watch item；
- 不生成 todo item。

### 场景 B：no match

输入：

- `GROUP999` text message
- 任意未匹配文本
- complete `RawAssetBundle`

结果：

- 不生成 workflow task；
- 生成 `TodoItem(category=no_match)`；
- reason 清晰，保留原始 match 失败原因。

### 场景 C：incomplete asset bundle

输入：

- `GROUP001` image message
- `text: 检装车通知单`
- 缺失 OCR JSON 的 `RawAssetBundle`

结果：

- 仍可生成匹配到的 workflow task；
- 额外生成 `TodoItem(category=incomplete_registration)`；
- reason 明确引用缺失路径。

## 4. 已阅读文件清单

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `reports/raw_asset_bundle_r9_20260525.md`
- `src/ops_hub/sop/monitoring_plan_matcher.py`
- `src/ops_hub/sop/raw_asset_bundle.py`
- `tests/functional/test_monitoring_plan_matcher.py`
- `tests/functional/test_raw_asset_bundle_registration.py`
- `src/ops_hub/sop/monitoring_plan_compiler.py`
- `src/ops_hub/models/project_sop.py`
- `tests/functional/test_sop_data_hub_source_supervision.py`

## 5. 测试命令与结果

```bash
pytest tests/functional/test_workflow_task_queue.py -v
pytest tests/functional -v
```

结果：

- `tests/functional/test_workflow_task_queue.py` -> `3 passed`
- `tests/functional -v` -> `22 passed, 1 skipped`
- skip 仍然是 `test_sop_data_hub_can_supervise_wx_ops_agent_and_rail95306_sync`

## 6. 明确未做

本轮未增加：

- runtime
- wx-ops-agent
- 数据库
- report sending
- delivery
- 真实跨模块监督
- 95306 接入
- OCR 执行
- asset 移动/复制
- R11 / R12 内容

## 7. 改动文件

- `src/ops_hub/sop/workflow_task.py`
- `tests/functional/test_workflow_task_queue.py`
- `reports/workflow_task_queue_r10_20260525.md`
- `reports/github_audit_workflow_task_queue_r10_20260525.md`

## 8. 下一步建议

进入 R11：`Report template and recipient resolver`。

仅在 order 明确允许后再做，不提前碰 delivery / runtime / report sending。
