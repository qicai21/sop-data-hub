# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R7`

## 0. Standing workflow rule

This order is the authoritative working instruction for this branch.

Hermes should not rely on long chat prompts for execution details. Chat prompts should only identify the repository, branch, and this order file.

Every work round must:

1. read this order;
2. execute only the current round task;
3. write a report;
4. commit and push;
5. reply with the required Execution Result format.

## 1. Branch purpose

This branch moves from real SOP markdown fixtures toward a first real monitoring strategy preview.

The desired chain is:

```text
real SOP markdown fixtures
  ↓
load / normalize
  ↓
compile with SopMonitoringPlanCompiler
  ↓
preview WeChat group monitoring strategy list
```

This branch must remain small and practical. Do not over-engineer.

## 2. Target real SOP projects

The four target projects are:

```text
1. 中唐特钢
2. 朝阳钢铁
3. 吉林金钢 / 吉林金刚
4. 九三大豆
```

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

Do not implement:

```text
runtime daemon
source publisher
real cross-module supervision
DB migration
actual WeChat/95306 integration
```

## 4. Required reading before work

Read current branch context:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
reports/real_sop_monitoring_plan_preview_20260525.md
src/ops_hub/models/project_sop.py
src/ops_hub/sop/monitoring_plan_preview.py
src/ops_hub/sop/monitoring_plan_compiler.py
tests/functional/test_real_sop_monitoring_plan_preview.py
tests/functional/test_sop_normalizer.py
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

## 7. Current round task: R7 accept generated preview as SOP-faithful baseline

### 7.1 Purpose

R7 is an acceptance/documentation round.

The user reviewed the R6 monitoring plan and accepted the principle:

```text
SOP 写了什么，系统就解析什么；
SOP 没写清楚的，不要替它脑补。
```

Therefore, R7 should freeze the current preview as a SOP-faithful baseline rather than trying to make vague SOP content more specific.

### 7.2 Important acceptance principle

Do not narrow broad items unless the SOP itself becomes more specific.

Example:

```text
GROUP013 / 数据单发群 -> 文字
```

This may look broad, but if the current 九三 SOP only exposes that level of detail, the system should preserve it rather than inventing:

```text
到站消息
铁路消息
发运动态
```

unless those exact meanings are explicitly represented in the SOP / normalized input.

### 7.3 Scope

This round should not change normalizer or compiler behavior.

Add a short acceptance note/report that records:

1. R6 generated plan is accepted as a SOP-faithful baseline;
2. broad entries are acceptable when broadness comes from the SOP text;
3. the system should not infer missing business detail;
4. future specificity should come from editing SOP documents, not hidden parser logic;
5. current output is preview-only and not runtime integration.

### 7.4 Deliverable

Add report:

```text
reports/real_sop_monitoring_plan_acceptance_20260525.md
```

The report must include:

- accepted baseline plan reference;
- acceptance principle;
- examples of accepted broad entries;
- explicit non-goals;
- recommended next step.

Optional GitHub audit summary:

```text
reports/github_audit_real_sop_monitoring_plan_acceptance_20260525.md
```

### 7.5 Tests

R7 is documentation-only.

No tests are required if no code/test files are changed.

If any code/test file changes, run:

```bash
pytest tests/functional -v
```

### 7.6 Commit requirements

Commit message:

```text
docs: accept real sop monitoring plan baseline
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
report: reports/real_sop_monitoring_plan_acceptance_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command or not run with reason>

summary:
- <what baseline was accepted>
- <what was not changed>
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
- narrow broad entries by inference;
- change normalizer/compiler behavior;
- merge this branch.

## 10. Next planned order update

After R7 is reviewed, likely next options are:

```text
A. stop this branch and prepare merge/PR review;
B. add a small CLI/report command to regenerate the preview;
C. edit SOP source documents in prompts_and_reports if more specificity is desired.
```

Do not proceed automatically.
