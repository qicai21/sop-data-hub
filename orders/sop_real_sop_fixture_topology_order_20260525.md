# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R2`

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

This branch continues from the completed SOP Monitoring Plan Compiler branch, but the goal is different.

The previous branch proved compiler behavior using synthetic in-test SOP dictionaries.

This branch must move toward real business material:

```text
真实项目 SOP 文件 from qicai21/prompts_and_reports
  ↓
复制/固化为 ops-data-hub functional test fixtures
  ↓
用真实 SOP fixture 驱动 loader / compiler / topology 重构
```

Do not create a new fictional SOP set unless a required real SOP is truly missing and must be represented by a clearly marked placeholder.

## 2. Target real SOP projects

The four target projects are:

```text
1. 中唐特钢
2. 朝阳钢铁
3. 吉林金钢 / 吉林金刚
4. 九三大豆
```

Important naming note:

- The user mentioned both `吉林金刚` and previous project material used `吉林金钢`.
- Hermes must not guess. It should search and report the exact names found in repository files.
- If both names appear, record whether they refer to the same project or different labels.

## 3. Source repository for real SOP files

The real SOP files are expected to live in:

```text
qicai21/prompts_and_reports
```

Not primarily in `qicai21/ops-data-hub`.

Hermes should inspect `prompts_and_reports` for the four target SOP files and then plan how to copy stable fixture copies into `ops-data-hub`.

Known relevant branch to inspect first:

```text
jilin-jingang-jinzhou-sop-init-20260521
```

Also inspect `main` if needed, because `prompts_and_reports` may have diverged branches.

Do not modify `prompts_and_reports` in this round. Treat it as a read-only source of truth.

## 4. Current conceptual goal

The goal is to answer and prepare for:

```text
Can ops-data-hub use the four real SOP files from prompts_and_reports as functional test fixtures,
and from them compile a WeChat group monitoring strategy list?
```

The desired future output is a real monitoring plan such as:

```text
微信群 A:
  - watch 出港计划通知单 for projects: 中唐特钢, 朝阳钢铁, ...
  - watch 检装车通知单 for projects: ...

微信群 B:
  - watch 文字放货消息 for projects: ...
```

However, this R1 round does not need to implement that full output yet.

## 5. Scope boundary

Main working repository:

```text
qicai21/ops-data-hub
branch: codex/sop-real-sop-topology-audit-20260525
local path: ~/projects/repos/sop-data-hub
```

Read-only source repository:

```text
qicai21/prompts_and_reports
branches to inspect: main and jilin-jingang-jinzhou-sop-init-20260521
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

## 6. Required reading before work

Read previous compiler context in `ops-data-hub`:

```text
orders/sop_data_hub_runtime_order_20260525.md
docs/architecture/sop_data_hub_runtime_rearchitecture_20260525.md
src/ops_hub/sop/monitoring_plan_compiler.py
tests/functional/test_sop_monitoring_plan_compiler.py
reports/sop_data_hub_runtime_r4_audit_20260525.md
```

Then inspect `prompts_and_reports` for real SOP files.

Suggested search terms:

```text
中唐
中唐特钢
朝阳
朝阳钢铁
吉林金钢
吉林金刚
九三
九三大豆
SOP
sop
jilin
chaoyang
zhongtang
jiusan
```

## 7. Current round task: R1 real SOP discovery and fixture plan

### 7.1 Locate real SOP files in prompts_and_reports

Search `qicai21/prompts_and_reports` first.

For each of the four target projects, report:

```text
project name
source repository
source branch
candidate SOP file path(s)
file format
whether content appears complete enough for functional fixture use
```

If a real SOP file is not found in `prompts_and_reports`, do not fabricate it. Instead report:

```text
MISSING_IN_PROMPTS_AND_REPORTS
```

### 7.2 Check whether copies already exist in ops-data-hub

After locating source SOPs in `prompts_and_reports`, check whether `ops-data-hub` already contains any copies or equivalents.

Report:

```text
already_present_in_ops_data_hub: yes/no
existing path if yes
```

### 7.3 Decide fixture strategy

If real SOP files are found, propose copying them into functional fixture paths such as:

```text
tests/fixtures/sops/zhongtang_special_steel_sop.md
tests/fixtures/sops/chaoyang_steel_sop.md
tests/fixtures/sops/jilin_jingang_sop.md
tests/fixtures/sops/jiusan_soybean_sop.md
```

R1 should not copy files unless all source paths are unambiguous and content is stable. The preferred R1 output is an audit and fixture plan.

### 7.4 Identify required loader contract

From the discovered SOP files, determine what a future loader must extract:

```text
project_id
project_name
sop_nodes
monitoring requirements
channel
wechat group identifier or group name
input_type
message/document type
text patterns / document patterns
target SOP node id
```

If current SOP files are written in prose and do not contain structured `monitoring` sections, report the missing fields.

### 7.5 Topology awareness audit

Audit whether `ops-data-hub` currently documents or encodes the existence of:

```text
ordinary freight dashboard / 普通货物看板
jiusan soybean cycle dashboard / 九三大豆循环运输看板
separate databases or data stores used by these dashboards
```

Search `ops-data-hub` for:

```text
普通货物
普通货运
看板
dashboard
九三
jiusan
sqlite
db
agent.db
jiusan_cycle.db
```

Report only facts found in repositories. Do not infer unstated database topology.

## 8. Deliverables for R1

Add a report in `ops-data-hub`:

```text
reports/real_sop_fixture_topology_audit_20260525.md
```

The report must include:

1. target project list;
2. discovered SOP file paths in `prompts_and_reports` or `MISSING_IN_PROMPTS_AND_REPORTS` for each project;
3. source branch for each discovered SOP;
4. whether each SOP can be used directly as a test fixture;
5. whether equivalent copies already exist in `ops-data-hub`;
6. proposed fixture paths;
7. required future loader contract;
8. missing structured fields;
9. repository evidence for ordinary freight dashboard;
10. repository evidence for jiusan soybean cycle dashboard;
11. repository evidence for database/store separation;
12. recommended next order.

Optional: add a GitHub audit summary report if useful:

```text
reports/github_audit_real_sop_fixture_topology_20260525.md
```

## 9. Tests for R1

No code changes are required in R1.

If no code is modified, tests are optional. If any code or test file is modified, run:

```bash
pytest tests/functional -v
```

## 10. Commit requirements

Commit message:

```text
docs: audit real sop fixture topology
```

Push to:

```text
origin codex/sop-real-sop-topology-audit-20260525
```

## 11. Hermes response format

After completion, reply:

```text
Execution Result

branch: codex/sop-real-sop-topology-audit-20260525
commit: <commit sha>
PR: <PR number if opened, or none>
order: orders/sop_real_sop_fixture_topology_order_20260525.md
report: reports/real_sop_fixture_topology_audit_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command or not run with reason>

summary:
- <what was found>
- <what is missing>
- <next recommended order/update>
```

## 12. Forbidden actions

Do not:

- invent fictional SOP content;
- rewrite real SOPs in R1;
- modify `prompts_and_reports`;
- modify `wx-ops-agent`;
- modify `rail95306-sync`;
- implement runtime/publisher/source supervision;
- modify database schema;
- alter production/server deployment;
- merge PR #3;
- merge this branch.

## 13. Next planned order update

After R1 is reviewed through GitHub, likely next step:

```text
R2: copy confirmed real SOP files from prompts_and_reports into tests/fixtures/sops/ and add a loader contract test.
```

Only proceed after review.

## 14. Current round task: R2 real SOP fixture copy and loader contract

### 14.1 Scope

- Copy the confirmed real SOP markdown files from `qicai21/prompts_and_reports` into `tests/fixtures/sops/`.
- Preserve the source SOP content exactly; do not invent or rewrite SOP text.
- Add a loader contract test that validates the copied fixtures still expose the expected SOP contract fields as markdown source material.

### 14.2 Required tests

If any test file changes, run:

```bash
pytest tests/functional -v
```

### 14.3 Forbidden in R2

- modifying `prompts_and_reports`
- modifying runtime / publisher / wx-ops-agent / rail95306-sync
- expanding into database schema or production deployment
