# Order: Real SOP Fixtures and System Lifecycle Closeout

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R12`

## 0. Standing workflow rule

This order is the authoritative working instruction for this branch.

Hermes should not rely on long chat prompts for execution details. Chat prompts should only identify the repository, branch, and this order file.

Every work round must:

1. read this order;
2. execute only the current round task;
3. write a report;
4. commit and push;
5. reply with the required Execution Result format.

## 1. Current lifecycle state

The branch now has a local, functional-test based chain:

```text
real SOP fixtures
  ↓
SopNormalizer
  ↓
SopMonitoringPlanCompiler
  ↓
wechat_monitoring_plan
  ↓
MessageEvent matcher
  ↓
RawAssetBundle registration
  ↓
WorkflowTask / TodoItem planner
  ↓
ReportIntent resolver
```

R12 is the final planned round in this five-round window. It should close the lifecycle locally by adding delivery result simulation and a complete lifecycle test/report.

## 2. Ordinary freight scope

Primary scope:

```text
普通货运看板主线：
- 中唐特钢
- 朝阳钢铁
- 吉林金钢 / 吉林金刚
```

九三大豆 remains out of the current workflow design.

## 3. Scope boundary

Main working repository:

```text
qicai21/ops-data-hub
branch: codex/sop-real-sop-topology-audit-20260525
local path: ~/projects/repos/sop-data-hub
```

Do not modify:

```text
wx-ops-agent
rail95306-sync
production/server deployment
prompts_and_reports
```

Do not implement in R12:

```text
real WeChat sending
real wx-ops-agent adapter
DB writes
runtime daemon
95306 integration
OCR execution
asset file copy/move
```

## 4. Required reading before work

Read current branch context:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
reports/workflow_task_queue_r10_20260525.md
reports/report_intent_r11_20260525.md
src/sop_hub/sop/workflow_task.py
src/sop_hub/sop/report_intent.py
src/sop_hub/sop/monitoring_plan_matcher.py
src/sop_hub/sop/raw_asset_bundle.py
tests/functional/test_workflow_task_queue.py
tests/functional/test_report_intent_resolver.py
```

## 5. Git round protocol

Each work round must:

```bash
cd ~/projects/repos/sop-data-hub
git fetch origin
git pull --ff-only origin codex/sop-real-sop-topology-audit-20260525
git status
git branch --show-current
```

Then execute only the current round task.

## 6. Completed rounds

- R1: found real SOPs and audited topology.
- R2: copied four real SOPs into `tests/fixtures/sops/`.
- R3: added minimal markdown loader contract.
- R4: audited loader boundary.
- R5: implemented minimal `SopNormalizer` for real SOP markdown fixtures.
- R6: generated first real SOP monitoring plan preview from the four fixtures.
- R7: accepted R6 plan as SOP-faithful baseline.
- R8: implemented local `MessageEvent -> monitoring plan matcher`.
- R9: implemented local `RawAssetBundle` registration and binding protocol.
- R10: implemented local `WorkflowTask / TodoItem` planner.
- R11: implemented local `WorkflowTask -> ReportIntent` resolver.

## 7. Current round task: R12 DeliveryResult and lifecycle closeout

### 7.1 Purpose

R12 should close the local lifecycle loop.

It should answer:

```text
Given a ReportIntent, how does SOP Data Hub record a simulated send result,
close a successful workflow task, or create a todo item for failure?
```

It should also provide one full local lifecycle test from message event to closeout.

### 7.2 Required concepts

Add minimal dataclasses or dictionaries:

```text
DeliveryResult
- delivery_id
- message_id
- project_id
- target_sop_node
- report_type
- recipient_target
- status
- confirmation_ref
- error

LifecycleCloseout
- workflow_task
- report_intent
- delivery_result
- status
- todo_items
- reason
```

Recommended statuses:

```text
DeliveryResult.status:
- sent
- failed
- skipped

LifecycleCloseout.status:
- closed
- delivery_failed
- report_intent_incomplete
```

Keep these as local Python objects. Do not add DB models.

### 7.3 Required behavior

Implement a thin local closeout function that can:

1. Accept a `WorkflowTask` and `ReportIntent`.
2. If `ReportIntent.status == ready` and simulated delivery succeeds, return closeout status `closed`.
3. If delivery fails, return closeout status `delivery_failed` and create a todo item.
4. If `ReportIntent.status != ready`, do not simulate delivery; create a todo item for incomplete report intent.
5. Preserve links to message_id, project_id, target_sop_node, report_type, recipient_target, and confirmation/error.

### 7.4 Full lifecycle test

Add one functional test that proves the ordinary freight local chain can run:

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
LifecycleCloseout(status=closed)
```

Use one simple ordinary freight case, for example:

```text
GROUP001 image message
text: 出港计划通知单
project candidate: chaoyang_steel or zhongtang_special_steel
```

This is still simulated; do not send anything.

### 7.5 Additional test scenarios

At minimum, add functional tests for:

#### Scenario A: successful delivery closes task

Expected:

- `DeliveryResult.status = sent`
- `LifecycleCloseout.status = closed`
- no todo item

#### Scenario B: failed delivery creates todo item

Expected:

- `DeliveryResult.status = failed`
- `LifecycleCloseout.status = delivery_failed`
- todo item exists with clear reason

#### Scenario C: incomplete report intent creates todo item without delivery

Expected:

- no fake successful delivery
- closeout status `report_intent_incomplete`
- todo item references missing fields

### 7.6 Allowed files

Implementation should be limited to:

```text
src/sop_hub/sop/delivery_result.py
src/sop_hub/sop/workflow_task.py
src/sop_hub/sop/report_intent.py
```

Tests:

```text
tests/functional/test_lifecycle_closeout.py
```

Reports:

```text
reports/lifecycle_closeout_r12_20260525.md
reports/github_audit_lifecycle_closeout_r12_20260525.md
```

### 7.7 Tests to run

Run:

```bash
pytest tests/functional/test_lifecycle_closeout.py -v
pytest tests/functional -v
```

Expected result:

```text
all functional tests pass except the intentional source supervision skip
```

### 7.8 Report requirement

Add report:

```text
reports/lifecycle_closeout_r12_20260525.md
```

The report must include:

- what was implemented;
- the full local lifecycle chain;
- the three closeout test scenarios;
- explicit note that no real sending, wx-ops-agent integration, DB, runtime, asset movement, OCR, or 95306 was added;
- remaining gaps before real `wx-ops-agent` integration.

### 7.9 Commit requirements

Commit message:

```text
feat: add lifecycle delivery closeout
```

Push to:

```text
origin codex/sop-real-sop-topology-audit-20260525
```

## 8. Hermes response format

After completion, reply:

```text
Execution Result

branch: codex/sop-real-sop-topology-audit-20260525
commit: <commit sha>
PR: none
order: orders/sop_real_sop_fixture_topology_order_20260525.md
report: reports/lifecycle_closeout_r12_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command -> result>

summary:
- <what delivery/closeout behavior was implemented>
- <whether the full local lifecycle test passes>
- <what was not implemented>
- <remaining gaps before wx-ops-agent integration>
```

## 9. Forbidden actions

Do not:

- modify `prompts_and_reports`;
- modify `wx-ops-agent`;
- modify `rail95306-sync`;
- implement real runtime/publisher/source supervision;
- modify database schema;
- alter production/server deployment;
- call external services;
- use OCR or AI extraction;
- move/copy raw assets;
- send real reports;
- merge this branch.
