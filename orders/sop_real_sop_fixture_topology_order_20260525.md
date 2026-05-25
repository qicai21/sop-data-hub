# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R10`

## 0. Standing workflow rule

This order is the authoritative working instruction for this branch.

Hermes should not rely on long chat prompts for execution details. Chat prompts should only identify the repository, branch, and this order file.

Every work round must:

1. read this order;
2. execute only the current round task;
3. write a report;
4. commit and push;
5. reply with the required Execution Result format.

## 1. Remaining rounds plan: R10-R12

The branch now has:

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
```

Three rounds remain in the current planning window.

### R10: WorkflowTask / TodoQueue

Convert matched or unmatched message events into local workflow tasks or todo items.

Goal:

```text
MessageEvent + RawAssetBundle + MessageMatchResult
  ↓
WorkflowTask / TodoItem
```

### R11: Report template and recipient resolver

Resolve which report template and recipient belong to a project/node/task.

Goal:

```text
WorkflowTask
  ↓
ReportIntent
  ↓
template path + recipient target + required fields
```

### R12: Delivery result and lifecycle closeout

Simulate delivery result confirmation and failure routing.

Goal:

```text
ReportIntent
  ↓
DeliveryResult
  ↓
closed task or todo item
```

Do not implement R11 or R12 during R10.

## 2. Ordinary freight scope

Primary scope:

```text
普通货运看板主线：
- 中唐特钢
- 朝阳钢铁
- 吉林金钢 / 吉林金刚
```

九三大豆 remains out of the current workflow design. Keep existing fixtures/tests intact, but do not let 九三 drive R10-R12.

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

Do not implement in R10:

```text
runtime daemon
source publisher
real cross-module supervision
DB migration
actual WeChat/95306 integration
report sending
asset file copy/move
OCR execution
```

## 4. Required reading before work

Read current branch context:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
reports/message_lifecycle_matcher_r8_20260525.md
reports/raw_asset_bundle_r9_20260525.md
src/ops_hub/sop/monitoring_plan_matcher.py
src/ops_hub/sop/raw_asset_bundle.py
tests/functional/test_monitoring_plan_matcher.py
tests/functional/test_raw_asset_bundle_registration.py
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

## 6. Previous completed rounds

- R1: found real SOPs and audited topology.
- R2: copied four real SOPs into `tests/fixtures/sops/`.
- R3: added minimal markdown loader contract.
- R4: audited loader boundary.
- R5: implemented minimal `SopNormalizer` for real SOP markdown fixtures.
- R6: generated first real SOP monitoring plan preview from the four fixtures.
- R7: accepted R6 plan as SOP-faithful baseline.
- R8: implemented local `MessageEvent -> monitoring plan matcher`.
- R9: implemented local `RawAssetBundle` registration and binding protocol.

## 7. Current round task: R10 WorkflowTask and TodoQueue

### 7.1 Purpose

R10 should define what happens after a message is matched or not matched.

It should answer:

```text
Given a MessageEvent, RawAssetBundle, and MessageMatchResult,
what workflow task or todo item should SOP Data Hub create?
```

This is still local and functional-test based. No database writes, no runtime daemon, no real wx-ops-agent integration.

### 7.2 Required concepts

Add minimal dataclasses or dictionaries:

```text
WorkflowTask
- task_id
- message_id
- group_id
- project_id
- target_sop_node
- watch_item
- raw_asset_bundle
- status
- reason

TodoItem
- todo_id
- message_id
- group_id
- category
- reason
- raw_asset_bundle
- suggested_action
- status
```

Recommended categories:

```text
no_match
missing_asset
ambiguous_project
incomplete_registration
```

Keep these as local Python objects. Do not add DB models.

### 7.3 Required behavior

Implement a thin local planner that can:

1. Create one `WorkflowTask` per matched project/node from a successful `MessageMatchResult`.
2. Create a `TodoItem` if no match is found.
3. Create a `TodoItem` if the bound raw asset bundle is incomplete.
4. Preserve links to message id, group id, watch item, project id, target SOP node, and raw asset bundle.
5. Do not deduplicate across historical messages; this round is single-message only.

### 7.4 Test scenarios

At minimum, add functional tests for:

#### Scenario A: matched 出港计划通知单 creates workflow tasks

Input:

```text
GROUP001 image message
text: 出港计划通知单
complete RawAssetBundle
```

Expected:

- creates workflow tasks for matched ordinary freight projects;
- each task has message id, group id, project id, target SOP node, watch item;
- no todo item for this case.

#### Scenario B: no match creates todo item

Input:

```text
GROUP999 text/image message
```

Expected:

- no workflow task;
- creates todo item category `no_match`;
- reason is clear.

#### Scenario C: incomplete asset bundle creates todo item

Input:

```text
GROUP001 image message
text: 检装车通知单
RawAssetBundle with missing OCR JSON
```

Expected:

- workflow task may still be created if match exists;
- todo item category `incomplete_registration` or `missing_asset` is created;
- warning/reason references missing path.

### 7.5 Allowed files

Implementation should be limited to:

```text
src/ops_hub/sop/workflow_task.py
src/ops_hub/sop/monitoring_plan_matcher.py
src/ops_hub/sop/raw_asset_bundle.py
```

Tests:

```text
tests/functional/test_workflow_task_queue.py
```

Reports:

```text
reports/workflow_task_queue_r10_20260525.md
reports/github_audit_workflow_task_queue_r10_20260525.md
```

### 7.6 Tests to run

Run:

```bash
pytest tests/functional/test_workflow_task_queue.py -v
pytest tests/functional -v
```

Expected result:

```text
all functional tests pass except the intentional source supervision skip
```

### 7.7 Report requirement

Add report:

```text
reports/workflow_task_queue_r10_20260525.md
```

The report must include:

- what was implemented;
- how matched messages become workflow tasks;
- how no-match/incomplete assets become todo items;
- the three test scenarios;
- explicit note that no real wx-ops-agent, DB, runtime, asset movement, OCR, or report sending was added;
- next planned round R11.

### 7.8 Commit requirements

Commit message:

```text
feat: add workflow task queue planner
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
report: reports/workflow_task_queue_r10_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command -> result>

summary:
- <what task/todo behavior was implemented>
- <ordinary freight scenarios covered>
- <what was not implemented>
- <next recommended order/update>
```

## 9. Forbidden actions

Do not:

- modify `prompts_and_reports`;
- modify `wx-ops-agent`;
- modify `rail95306-sync`;
- implement runtime/publisher/source supervision;
- modify database schema;
- alter production/server deployment;
- call external services;
- use OCR or AI extraction;
- move/copy raw assets;
- send reports;
- implement report template resolver;
- implement delivery confirmation;
- merge this branch.

## 10. R11 preview

Do not implement R11 in this round.

R11 should resolve report template and recipient targets for workflow tasks:

```text
WorkflowTask
  ↓
ReportIntent
  - template path
  - report type
  - recipient target
  - required fields
```

R11 should still be local and functional-test based.
