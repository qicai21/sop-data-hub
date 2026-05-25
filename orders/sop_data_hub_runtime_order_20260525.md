# Order: SOP Data Hub Runtime Planning and Compiler Test Track

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-data-hub-runtime-plan-20260525`
Order mode: incremental
Current round: `R2`

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
reports/test_hygiene_fix_20260525.md
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

### R2: implement `SopMonitoringPlanCompiler.compile()`

R1-2 has been reviewed through GitHub and accepted. The compiler import contract now passes, and the remaining expected failure is `NotImplementedError` from `SopMonitoringPlanCompiler.compile()`.

This round implements the minimal compiler needed to satisfy:

```text
tests/functional/test_sop_monitoring_plan_compiler.py
```

#### 6.1 Implementation file

Only implement in:

```text
src/ops_hub/sop/monitoring_plan_compiler.py
```

Do not change `wx-ops-agent`, `rail95306-sync`, database schema, runtime daemon, or production/server deployment.

#### 6.2 Required behavior

`SopMonitoringPlanCompiler.compile(project_sops)` must:

1. accept a list of project SOP dictionaries;
2. read each `project_id`;
3. iterate each `sop_nodes[]` item;
4. iterate each node's `monitoring[]` requirements;
5. group WeChat requirements into:

```text
wechat_monitoring_plan[group_id]
```

6. preserve `group_name` when provided;
7. deduplicate identical watch items within the same group;
8. preserve all `candidate_projects` for each watch item;
9. preserve `target_sop_nodes` mapping from project id to node ids;
10. preserve `text_patterns` for text watch items;
11. return deterministic output suitable for tests and future source-agent plans.

#### 6.3 Watch item identity

A watch item should be considered identical within the same group when the following semantic fields match:

```text
input_type
document_type
message_type
```

For this round, this identity is enough.

Examples:

- `document + 出港计划通知单` in `group_1` for 中唐 and 朝阳 must become one watch item with two candidate projects.
- `document + 检装车通知单` in `group_1` for 中唐 and 朝阳 must become one watch item with two candidate projects.
- `text + 文字放货消息` in `group_3` must preserve its text patterns.

#### 6.4 Deterministic ordering

The compiler should keep deterministic order.

Recommended rule:

- groups appear in first-seen order;
- watch items appear in first-seen order;
- candidate projects appear in first-seen order;
- target SOP node lists appear in first-seen order;
- text patterns preserve first-seen order and should not duplicate values.

Do not sort Chinese strings unless necessary.

#### 6.5 Output shape

The functional test expects this top-level structure:

```python
{
    "wechat_monitoring_plan": {
        "group_1": {
            "group_name": "微信1号群",
            "watch_items": [
                {
                    "input_type": "document",
                    "document_type": "出港计划通知单",
                    "candidate_projects": ["zhongtang_special_steel", "chaoyang_steel"],
                    "target_sop_nodes": {
                        "zhongtang_special_steel": ["departure_plan_notice"],
                        "chaoyang_steel": ["departure_plan_notice"],
                    },
                }
            ],
        }
    }
}
```

For text watch items, preserve:

```python
"message_type": "文字放货消息"
"text_patterns": ["放货", "发运", "到港", "卸船"]
```

#### 6.6 Input validation policy for this round

Keep validation minimal.

- If `project_sops` is empty, return `{"wechat_monitoring_plan": {}}`.
- If a project lacks `sop_nodes`, skip it.
- If a node lacks `monitoring`, skip it.
- If a monitoring requirement uses a non-`wechat` channel, ignore it for now.
- If a WeChat requirement lacks `group_id`, skip it for now.

Do not introduce pydantic, database dependencies, or external service dependencies in this round.

#### 6.7 Report requirement

Add report:

```text
reports/sop_monitoring_plan_compiler_implementation_20260525.md
```

Report must include:

- files changed;
- implementation summary;
- output shape summary;
- validation/skipping policy;
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

Expected result after R2:

```text
tests/functional/test_sop_monitoring_plan_compiler.py -> 2 passed
tests/functional/test_sop_data_hub_source_supervision.py -> 1 skipped
tests/functional -> 2 passed, 1 skipped
```

## 8. Commit requirements

Commit message should be:

```text
feat: implement sop monitoring plan compiler
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
- hard-code test expected output;
- implement source supervision adapters in this round;
- add network/service dependencies.

## 10. Next planned order update

After R2 is complete and reviewed through GitHub, the next order update should be one of:

```text
R3-A: add compiler edge-case tests for duplicate text patterns and non-WeChat skipping.
```

or

```text
R3-B: design source monitoring plan publisher contract for wx-ops-agent without modifying wx-ops-agent.
```

Choose only after GitHub review of R2.
