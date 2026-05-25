# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R4`

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

Do not modify `prompts_and_reports`. Treat it as a read-only source of truth.

## 4. Scope boundary

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

## 5. Required reading before work

Read current branch context:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
reports/real_sop_fixture_topology_audit_20260525.md
reports/real_sop_fixture_topology_r2_20260525.md
reports/real_sop_fixture_topology_r3_20260525.md
src/ops_hub/models/project_sop.py
tests/functional/test_real_sop_fixture_contract.py
```

Also keep compiler boundary in mind:

```text
src/ops_hub/sop/monitoring_plan_compiler.py
tests/functional/test_sop_monitoring_plan_compiler.py
```

## 6. Git round protocol

Each work round must:

```bash
cd ~/projects/repos/sop-data-hub
git fetch origin
git pull --ff-only origin codex/sop-real-sop-topology-audit-20260525
git status
git branch --show-current
```

Then execute only the current round task.

## 7. Previous completed rounds

### R1: real SOP discovery and topology audit

Found four real SOPs in `qicai21/prompts_and_reports` and audited dashboard/database topology.

### R2: real SOP fixture copy and loader contract

Copied four real SOP markdown files into:

```text
tests/fixtures/sops/
```

Added fixture contract tests.

### R3: minimal markdown loader contract

Added minimal markdown loader behavior. Current loader only returns raw path, title, and content. It intentionally does not parse full SOP business semantics.

## 8. Current round task: R4 loader schema boundary audit

### 8.1 Purpose

R4 is an audit/boundary round. It exists to prevent the markdown loader from becoming a hidden business parser or a second compiler.

Do not add business parsing in R4.

Do not connect loader output to the compiler in R4.

Do not implement monitoring plan generation in R4.

### 8.2 Audit questions

Write a report answering:

1. What should the markdown SOP loader be responsible for?
2. What should it explicitly not be responsible for?
3. What fields should a near-term raw markdown loader output?
4. What should be left to a future `SopNormalizer` or similar layer?
5. What should remain solely the responsibility of `SopMonitoringPlanCompiler`?
6. Does current `load_markdown_sop_fixture()` stay within loader scope?
7. Does current `test_real_sop_fixture_contract.py` overreach or remain appropriately narrow?
8. What is the safest next step after R4?

### 8.3 Proposed responsibility boundary

Loader may own:

```text
file path
raw content
first markdown title
basic markdown heading list
raw group tokens such as [GROUP001]
raw data-source blocks if directly extractable without inference
```

Loader must not own:

```text
business semantic inference
SOP node normalization
monitoring requirement generation
project merging
Wechat monitoring plan compilation
compiler invocation
database reads/writes
runtime scheduling
```

Future normalizer may own:

```text
turn raw SOP markdown structure into normalized project_sops
map headings/tables to sop_nodes
extract explicit monitoring requirements if present
flag missing structured fields
produce data suitable for compiler input
```

Compiler owns:

```text
merge normalized project_sops by channel/group
remove duplicated watch items
preserve candidate_projects
generate target_sop_nodes mapping
return channel monitoring plan
```

### 8.4 Deliverable

Add report:

```text
reports/real_sop_loader_boundary_audit_20260525.md
```

The report must include:

- loader responsibilities;
- loader non-responsibilities;
- proposed raw loader schema;
- proposed normalizer responsibilities;
- compiler responsibilities;
- assessment of current implementation;
- assessment of current tests;
- recommended next order.

Optional GitHub audit summary:

```text
reports/github_audit_real_sop_loader_boundary_20260525.md
```

### 8.5 Tests

R4 is an audit-only round.

No tests are required if no code/test files are changed.

If any code/test file is changed, run:

```bash
pytest tests/functional -v
```

### 8.6 Commit requirements

Commit message:

```text
docs: audit real sop loader boundary
```

Push to:

```text
origin codex/sop-real-sop-topology-audit-20260525
```

## 9. Hermes response format

After completion, reply:

```text
Execution Result

branch: codex/sop-real-sop-topology-audit-20260525
commit: <commit sha>
PR: none
order: orders/sop_real_sop_fixture_topology_order_20260525.md
report: reports/real_sop_loader_boundary_audit_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command or not run with reason>

summary:
- <what was audited>
- <what boundary was set>
- <next recommended order/update>
```

## 10. Forbidden actions

Do not:

- modify `prompts_and_reports`;
- modify `wx-ops-agent`;
- modify `rail95306-sync`;
- implement runtime/publisher/source supervision;
- modify database schema;
- alter production/server deployment;
- connect loader to compiler;
- generate monitoring plans from markdown;
- add business semantic parsing to loader;
- invent fictional SOP content;
- merge this branch.

## 11. Next planned order update

After R4 is reviewed through GitHub, possible next step:

```text
R5: Add a very small raw heading/group-token extraction test if the boundary report approves it.
```

Do not proceed without review.
