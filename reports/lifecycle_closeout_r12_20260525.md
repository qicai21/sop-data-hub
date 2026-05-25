# Report: R12 Lifecycle Closeout

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R12 |
| 执行日期 | 2026-05-25 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前 commit | 29c3a55（本轮提交） |
| Python 版本 | Python 3.11.15 |

## 1. 结论

本轮实现了本地生命周期收尾链路：

```text
MessageEvent
  ↓
RawAssetBundle
  ↓
monitoring plan match
  ↓
WorkflowTask
  ↓
ReportIntent
  ↓
DeliveryResult
  ↓
LifecycleCloseout(status=closed / delivery_failed / report_intent_incomplete)
```

新增的是纯本地模拟收尾，不包含真实发送、runtime、wx-ops-agent、数据库、OCR、95306 或资产搬运。

## 2. 已实现内容

- 新增 `DeliveryResult` 与 `LifecycleCloseout` 本地对象。
- 新增 `simulate_delivery_result()`：只做本地模拟，不对外发送。
- 新增 `closeout_lifecycle()`：
  - `ReportIntent.status == ready` 且模拟成功时返回 `closed`；
  - 模拟失败时返回 `delivery_failed` 并生成 todo item；
  - `ReportIntent.status != ready` 时跳过模拟发送，直接生成 `report_intent_incomplete` 的 todo item。
- 增加完整功能测试，覆盖：
  - 成功闭环；
  - 失败生成 todo；
  - report intent 不完整时不模拟发送。

## 3. 全链路测试

使用了普通货运场景的本地模拟链路：

```text
GROUP001 image message
text: 出港计划通知单
project: chaoyang_steel
```

链路验证结果：

- `MessageEvent` 正常构造；
- `RawAssetBundle` 注册成功；
- `match_message_event()` 命中 monitoring plan；
- `build_workflow_task_queue()` 生成 `WorkflowTask`；
- `resolve_report_intent()` 生成 `ReportIntent(status=ready)`；
- `closeout_lifecycle()` 生成 `DeliveryResult(status=sent)`；
- `LifecycleCloseout(status=closed)` 返回成功闭环。

## 4. 三个 closeout 场景

### 场景 A：成功 delivery closes task

- `DeliveryResult.status = sent`
- `LifecycleCloseout.status = closed`
- `todo_items = []`

### 场景 B：failed delivery creates todo item

- `DeliveryResult.status = failed`
- `LifecycleCloseout.status = delivery_failed`
- `todo_items` 含清晰失败原因

### 场景 C：incomplete report intent creates todo item without delivery

- 不模拟成功发送
- `LifecycleCloseout.status = report_intent_incomplete`
- `todo_items` 引用缺失字段

## 5. 测试结果

- `pytest tests/functional/test_lifecycle_closeout.py -v` → 3 passed
- `pytest tests/functional -v` → 27 passed, 1 skipped

跳过项仍然是预期中的 source supervision 占位测试。

## 6. 失败 / skip 原因

- 无新增失败。
- `tests/functional/test_sop_data_hub_source_supervision.py` 仍然 skipped，因为 runtime source supervision adapter 还未设计，且本轮明确禁止进入真实联动。

## 7. 改动文件

### 新增
- `src/ops_hub/sop/delivery_result.py`
- `tests/functional/test_lifecycle_closeout.py`
- `reports/lifecycle_closeout_r12_20260525.md`
- `reports/github_audit_lifecycle_closeout_r12_20260525.md`

### 未修改
- `wx-ops-agent`
- `rail95306-sync`
- 数据库 schema
- runtime daemon
- OCR / 95306 / 真发送 / 资产搬运

## 8. 下一步建议

如果后续要继续推进，只能进入真实 source supervision 之前的下一层抽象设计；本轮不建议扩大到 runtime 或外部联动。

## 9. Git

- 当前分支：`codex/sop-real-sop-topology-audit-20260525`
- 当前提交：`29c3a55`
- 远端同步：`git fetch/pull` 尝试失败，HTTPS 返回 `SSL_ERROR_SYSCALL`，SSH 返回 `Permission denied (publickey)`；本轮以本地已实现提交为基线继续整理 report/audit。
- 本轮功能提交：`29c3a55`
