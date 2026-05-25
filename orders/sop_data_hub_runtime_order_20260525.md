# Order: SOP Data Hub Runtime Planning and Compiler Test Track

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-data-hub-runtime-plan-20260525`
Order mode: incremental

## 0. Standing workflow rule

This order is the authoritative working instruction for the current SOP data hub runtime planning branch.

Future work in the same stage should update this order incrementally instead of creating a new order for every conversation turn.

Hermes should not rely on long chat prompts for detailed execution instructions. Chat prompts should only tell Hermes which repository, branch, and order file to read.

## 1. Scope boundary

Only work in:

```text
qicai21/ops-data-hub
branch: codex/sop-data-hub-runtime-plan-20260525
local path: ~/projects/repos/sop-data-hub
```

Do not modify:

```text
wx-ops-agent
rail95306-sync
existing production/server ops-data-hub deployment
```

Cross-module supervision remains at planning/test-contract level only until a later explicit order.

## 2. Current branch purpose

This branch defines the architecture and tests for evolving `ops-data-hub` into `SOP data hub`.

The most important new capability is:

```text
SOP Monitoring Plan Compiler
```

It must compile project-level SOP monitoring requirements into channel-level monitoring plans.

Example:

```text
Project SOPs:
  中唐特钢 -> group_1 watches 出港计划通知单 / 检装车通知单
  朝阳钢铁 -> group_1 watches 出港计划通知单 / 检装车通知单; group_3 watches 出港计划通知单 / 文字放货消息
  九三 -> group_2 watches 到站消息

Compiled output:
  group_1 watches 出港计划通知单 once, with candidate projects 中唐+朝阳
  group_1 watches 检装车通知单 once, with candidate projects 中唐+朝阳
  group_2 watches 到站消息, with candidate project 九三
  group_3 watches 出港计划通知单 and 文字放货消息, with candidate project 朝阳
```

## 3. Required reading before work

Hermes must read these files before each work round in this branch:

```text
docs/architecture/sop_data_hub_runtime_rearchitecture_20260525.md
reports/sop_data_hub_runtime_plan_branch_20260525.md
tests/functional/test_sop_monitoring_plan_compiler.py
tests/functional/test_sop_data_hub_source_supervision.py
src/ops_hub/sop/monitoring_plan_compiler.py
orders/sop_data_hub_runtime_order_20260525.md
```

## 4. Git round protocol

Each work round must follow this sequence:

1. Confirm local path:

```bash
cd ~/projects/repos/sop-data-hub
pwd
git status
git branch --show-current
git log --oneline -5
```

2. Confirm branch is:

```text
codex/sop-data-hub-runtime-plan-20260525
```

3. Pull latest branch state:

```bash
git fetch origin
git pull --ff-only origin codex/sop-data-hub-runtime-plan-20260525
```

4. Read this order and required files.

5. Execute only the current round task listed in section 6.

6. Run required tests listed in section 7.

7. Write a report under `reports/`.

8. Commit and push.

9. Reply with an execution result summary containing branch, commit, PR, changed files, tests, and report path.

## 5. Hermes response format

After each round, Hermes must reply in this format:

```text
Execution Result

branch: <branch>
commit: <commit sha>
PR: #3 — Plan SOP data hub runtime and monitoring compiler tests
order: orders/sop_data_hub_runtime_order_20260525.md
report: <report path>
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command> -> <result>

summary:
- <what changed>
- <what did not change>
- <next recommended order/update>
```

Do not report completion based only on chat. Completion must be proven by git commit and pushed branch state.

## 6. Current round task

### Round 1: test hygiene and path correction

Current task only fixes test/report hygiene. Do not implement business logic.

#### 6.1 Correct local path references

The correct local path is:

```text
~/projects/repos/sop-data-hub
```

If previous reports mention:

```text
~/projects/sop-data-hub-test/ops-data-hub
```

update them to the correct path.

Files to update if present:

```text
reports/local_sop_data_hub_branch_bootstrap_20260525.md
reports/github_audit_local_sop_data_hub_branch_bootstrap_20260525.md
```

#### 6.2 Fix xpass in compiler functional test

File:

```text
tests/functional/test_sop_monitoring_plan_compiler.py
```

Problem:

`test_sop_monitoring_plan_compiler_import_contract_exists()` is marked `xfail`, but `SopMonitoringPlanCompiler` already exists.

Required change:

- remove `@pytest.mark.xfail` from this import contract test;
- keep the assertion that `SopMonitoringPlanCompiler is not None`;
- do not implement `compile()`.

Expected test state after this round:

```text
tests/functional/test_sop_monitoring_plan_compiler.py -> 1 failed, 1 passed
tests/functional/test_sop_data_hub_source_supervision.py -> 1 skipped
tests/functional -> 1 failed, 1 passed, 1 skipped
```

The remaining failure must be `NotImplementedError` from `SopMonitoringPlanCompiler.compile()`.

#### 6.3 Add report

Add a report:

```text
reports/test_hygiene_fix_20260525.md
```

The report must include:

- local path correction;
- test xpass root cause;
- files changed;
- test commands and results;
- confirmation that `compile()` was not implemented;
- next recommended order update.

## 7. Required tests for current round

Run:

```bash
pytest tests/functional/test_sop_monitoring_plan_compiler.py -v
pytest tests/functional/test_sop_data_hub_source_supervision.py -v
pytest tests/functional -v
```

Do not try to make the compiler functional test pass by implementing `compile()` in this round.

## 8. Commit requirements

Commit message should be:

```text
test: fix sop monitoring compiler test hygiene
```

Push to:

```text
origin codex/sop-data-hub-runtime-plan-20260525
```

## 9. Forbidden actions

Do not:

- implement `SopMonitoringPlanCompiler.compile()` in this round;
- edit `wx-ops-agent`;
- edit `rail95306-sync`;
- edit production/server deployment;
- change database schema;
- delete reconciler logic;
- rename repository;
- modify runtime daemon behavior;
- hard-code expected output into compiler implementation.

## 10. Next planned order update

After Round 1 is complete and reviewed through GitHub, the next order update should be:

```text
Round 2: Implement SopMonitoringPlanCompiler.compile() to satisfy the functional test.
```

Round 2 should still be limited to `ops-data-hub` and should not touch `wx-ops-agent` or `rail95306-sync`.
