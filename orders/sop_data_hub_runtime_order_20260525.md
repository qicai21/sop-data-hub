# Order: SOP Data Hub Runtime Planning and Compiler Test Track

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-data-hub-runtime-plan-20260525`
Order mode: incremental
Current round: `R3`

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

## 3. Required reading before work

Hermes must read these files before each work round in this branch:

```text
docs/architecture/sop_data_hub_runtime_rearchitecture_20260525.md
reports/sop_data_hub_runtime_plan_branch_20260525.md
tests/functional/test_sop_monitoring_plan_compiler.py
tests/functional/test_sop_data_hub_source_supervision.py
src/sop_hub/sop/monitoring_plan_compiler.py
orders/sop_data_hub_runtime_order_20260525.md
reports/sop_monitoring_plan_compiler_implementation_20260525.md
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

### R3: add near-scope basic compiler tests only

R2 has been reviewed through GitHub and accepted. `SopMonitoringPlanCompiler.compile()` now satisfies the main functional test.

This round should only add a few close-range/basic tests for the existing compiler behavior. Do not expand into source publisher, runtime daemon, cross-repo integration, adapter contracts, or monitoring of `wx-ops-agent` / `rail95306-sync`.

The goal is to protect the basic compiler behavior without making tests increasingly distant from the current feature.

#### 6.1 File to modify

Only modify:

```text
tests/functional/test_sop_monitoring_plan_compiler.py
```

If implementation changes are truly required to satisfy these tests, keep them minimal and limited to:

```text
src/sop_hub/sop/monitoring_plan_compiler.py
```

Do not add new modules.

#### 6.2 Required basic tests

Add tests for these close-range cases:

1. Empty input returns empty WeChat plan.

Expected:

```python
SopMonitoringPlanCompiler().compile([]) == {"wechat_monitoring_plan": {}}
```

2. Non-WeChat monitoring requirements are skipped.

Example input may include `channel: email` or `channel: rail95306`.

Expected:

```python
{"wechat_monitoring_plan": {}}
```

3. WeChat monitoring requirements without `group_id` are skipped.

Expected:

```python
{"wechat_monitoring_plan": {}}
```

4. Duplicate text patterns are deduplicated while preserving first-seen order.

Example:

```python
text_patterns: ["放货", "发运", "放货", "到港"]
```

Expected:

```python
["放货", "发运", "到港"]
```

5. Duplicate target SOP node ids are not repeated for the same project/watch item.

If the same project and node repeats the same WeChat document requirement, the output should keep only one node id in:

```python
target_sop_nodes[project_id]
```

#### 6.3 Keep tests close to compiler scope

Do not add tests for:

- publishing plans to `wx-ops-agent`;
- reading from `rail95306-sync`;
- runtime daemon cycles;
- database reads/writes;
- report generation;
- dashboard refresh;
- real WeChat group IDs;
- external services.

#### 6.4 Report requirement

Add report:

```text
reports/sop_monitoring_plan_compiler_basic_tests_20260525.md
```

Report must include:

- files changed;
- tests added;
- whether implementation changed;
- test commands and results;
- confirmation that no cross-module code was touched;
- next recommended order update.

## 7. Required tests for current round

Run:

```bash
pytest tests/functional/test_sop_monitoring_plan_compiler.py -v
pytest tests/functional/test_sop_data_hub_source_supervision.py -v
pytest tests/functional -v
```

Expected result after R3:

```text
tests/functional/test_sop_monitoring_plan_compiler.py -> all compiler tests passed
tests/functional/test_sop_data_hub_source_supervision.py -> 1 skipped
tests/functional -> all passed except the intentional source-supervision skip
```

## 8. Commit requirements

Commit message should be:

```text
test: add basic sop monitoring compiler coverage
```

Push to:

```text
origin codex/sop-data-hub-runtime-plan-20260525
```

## 9. Forbidden actions

Do not:

- edit `wx-ops-agent`;
- edit `rail95306-sync`;
- edit production/server deployment;
- change database schema;
- delete reconciler logic;
- rename repository;
- modify runtime daemon behavior;
- implement source supervision adapters;
- implement source plan publisher;
- add network/service dependencies;
- add broad architectural tests beyond the compiler's basic behavior.

## 10. Next planned order update

After R3 is complete and reviewed through GitHub, pause for review.

Do not automatically proceed to publisher/runtime work.

Possible next step, only after explicit approval:

```text
R4: decide whether to add a source-plan export contract or stop this branch for merge review.
```
