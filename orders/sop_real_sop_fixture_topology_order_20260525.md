# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R6`

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

Read-only source repository:

```text
qicai21/prompts_and_reports
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
reports/real_sop_normalizer_20260525.md
src/ops_hub/models/project_sop.py
tests/functional/test_sop_normalizer.py
src/ops_hub/sop/monitoring_plan_compiler.py
tests/functional/test_sop_monitoring_plan_compiler.py
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

## 7. Current round task: R6 real SOP monitoring plan preview

### 7.1 Purpose

R6 is the first end-to-end preview round.

It should answer:

```text
If we use the four real SOP fixtures, what WeChat group monitoring strategy list can the system generate today?
```

This is not production integration. It is a functional preview.

### 7.2 Scope

Use the existing pieces:

```text
tests/fixtures/sops/*.md
SopNormalizer
SopMonitoringPlanCompiler
```

Add only the thin glue needed to transform normalized SOPs into the compiler input shape, then generate a preview report.

### 7.3 Required implementation behavior

Implement a minimal adapter if needed, limited to `ops-data-hub`, that converts normalized SOP records into `project_sops` dictionaries accepted by `SopMonitoringPlanCompiler`.

The adapter may:

- read normalized project SOPs;
- convert `monitoring_entries` into `sop_nodes[].monitoring[]` shape;
- preserve `project_id` and `project_name`;
- use source group token as `group_id` when present;
- use group name if available;
- use document keywords as document watch items;
- use message keywords as text/message watch items.

The adapter must not:

- call WeChat;
- call 95306;
- write database records;
- start runtime daemon;
- publish config to source agents;
- infer hidden SOP meanings beyond what the normalizer already extracted.

### 7.4 Preview output requirement

Generate a markdown report:

```text
reports/real_sop_monitoring_plan_preview_20260525.md
```

The report must include:

1. input fixture list;
2. normalized project count;
3. generated WeChat group monitoring plan;
4. for each group:
   - group id/token;
   - group name if available;
   - watch items;
   - candidate projects;
   - target SOP node mapping or source node placeholder;
5. limitations / warnings;
6. whether the generated plan looks usable enough to continue.

The monitoring list should be readable by a human, not only JSON.

### 7.5 Test requirement

Add a small functional test proving the chain runs:

```text
real fixtures -> normalizer -> adapter -> compiler -> wechat_monitoring_plan
```

Suggested test file:

```text
tests/functional/test_real_sop_monitoring_plan_preview.py
```

Test expectations should stay basic:

- output contains `wechat_monitoring_plan`;
- output has at least one group;
- output has at least one watch item;
- at least one real project appears in candidate projects;
- no runtime/publisher/DB dependency is required.

Do not over-test exact business correctness yet. The user wants to see what the current system generates.

### 7.6 Allowed files

Implementation should be limited to one of:

```text
src/ops_hub/models/project_sop.py
src/ops_hub/sop/monitoring_plan_preview.py
```

Tests:

```text
tests/functional/test_real_sop_monitoring_plan_preview.py
```

Reports:

```text
reports/real_sop_monitoring_plan_preview_20260525.md
reports/github_audit_real_sop_monitoring_plan_preview_20260525.md
```

Order update:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
```

### 7.7 Tests to run

Run:

```bash
pytest tests/functional/test_real_sop_monitoring_plan_preview.py -v
pytest tests/functional -v
```

Expected result:

```text
all functional tests pass except intentional source supervision skip
```

### 7.8 Commit requirements

Commit message:

```text
feat: preview real sop monitoring plan
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
report: reports/real_sop_monitoring_plan_preview_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command -> result>

summary:
- <what plan was generated>
- <whether it looks usable>
- <key limitations>
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
- over-test exact final business correctness;
- merge this branch.

## 10. Next planned order update

After R6 is reviewed, decide whether to:

```text
A. accept the generated monitoring plan shape and clean it up;
B. adjust normalizer extraction rules lightly;
C. pause and review fixture SOP content manually.
```

Do not proceed automatically.
