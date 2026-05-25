# Order: Real SOP Fixtures and System Topology Audit

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-real-sop-topology-audit-20260525`
Order mode: incremental
Current round: `R9`

## 0. Standing workflow rule

This order is the authoritative working instruction for this branch.

Hermes should not rely on long chat prompts for execution details. Chat prompts should only identify the repository, branch, and this order file.

Every work round must:

1. read this order;
2. execute only the current round task;
3. write a report;
4. commit and push;
5. reply with the required Execution Result format.

## 1. Five-round goal from R8 to R12

The next five rounds should move SOP Data Hub toward supervising `wx-ops-agent` for ordinary freight projects.

Primary scope for these five rounds:

```text
普通货运看板主线：
- 中唐特钢
- 朝阳钢铁
- 吉林金钢 / 吉林金刚
```

九三大豆 is intentionally not the current optimization target. Keep its fixture and tests, but do not let 九三 drive the ordinary freight workflow design in R8-R12.

By the end of R12, the branch should define and test the core lifecycle:

```text
wx-ops-agent observed message
  ↓
SOP Data Hub receives message event
  ↓
raw image/json/text asset registered
  ↓
monitoring plan match
  ↓
project/node candidate produced
  ↓
workflow task created
  ↓
report template/recipient resolved
  ↓
delivery result recorded
  ↓
mismatch/failure falls into todo queue
```

R8 only starts this sequence. Do not implement later rounds early.

## 2. Scope boundary

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

Do not implement in R8:

```text
runtime daemon
source publisher
real cross-module supervision
DB migration
actual WeChat/95306 integration
report sending
asset file copy/move
```

## 3. Required reading before work

Read current branch context:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
reports/real_sop_monitoring_plan_preview_20260525.md
reports/real_sop_monitoring_plan_acceptance_20260525.md
src/ops_hub/models/project_sop.py
src/ops_hub/sop/monitoring_plan_preview.py
src/ops_hub/sop/monitoring_plan_compiler.py
tests/functional/test_real_sop_monitoring_plan_preview.py
tests/functional/test_sop_normalizer.py
```

## 4. Git round protocol

Each work round must:

```bash
cd ~/projects/repos/sop-data-hub
git fetch origin
git pull --ff-only origin codex/sop-real-sop-topology-audit-20260525
git status
git branch --show-current
```

Then execute only the current round task.

## 5. Previous completed rounds

- R1: found real SOPs and audited topology.
- R2: copied four real SOPs into `tests/fixtures/sops/`.
- R3: added minimal markdown loader contract.
- R4: audited loader boundary.
- R5: implemented minimal `SopNormalizer` for real SOP markdown fixtures.
- R6: generated first real SOP monitoring plan preview from the four fixtures.
- R7: accepted R6 plan as SOP-faithful baseline.

## 6. Current round task: R8 message lifecycle event and monitoring plan matcher

### 6.1 Purpose

R8 should verify that the generated monitoring plan is usable for message-level supervision.

It should answer:

```text
Given a simulated wx-ops-agent message event, can SOP Data Hub match it against the generated monitoring plan and produce candidate projects/nodes?
```

This is still a pure functional simulation. No real WeChat, no runtime daemon, no database writes.

### 6.2 Ordinary freight scope only

R8 should focus on ordinary freight projects:

```text
zhongtang_special_steel
chaoyang_steel
jilin_jingang_jinzhou
```

九三 can remain in fixtures and existing tests, but R8 matcher tests should not depend on 九三.

### 6.3 Required concepts

Add minimal data concepts if useful:

```text
MessageEvent
- message_id
- channel
- group_id
- message_type
- text
- image_path
- json_path
- received_at

MonitoringMatch
- group_id
- watch_item
- candidate_projects
- target_sop_nodes
- reason
```

Keep these as simple dataclasses or dictionaries. Do not introduce database models.

### 6.4 Required behavior

Use the existing preview chain:

```text
real SOP fixtures
  ↓
SopNormalizer / monitoring plan preview adapter
  ↓
SopMonitoringPlanCompiler
  ↓
wechat_monitoring_plan
```

Then implement a thin matcher that can match simulated message events.

Basic matching rules for R8:

1. Match by `group_id` first.
2. For document/image-style messages, match if text or extracted label contains a `document_type` watch item.
3. For text messages, match if text contains a `message_type` or one of `text_patterns`.
4. Return candidate projects and target SOP node mapping from the compiled plan.
5. If nothing matches, return no match and a reason.

### 6.5 Example scenarios to test

At minimum, add functional tests for:

#### Scenario A: GROUP001 出港计划通知单

Input:

```text
group_id: GROUP001
message_type: image
text: 出港计划通知单
```

Expected:

- matched watch item: `出港计划通知单`
- candidate projects include:
  - `zhongtang_special_steel`
  - `chaoyang_steel`
  - `jilin_jingang_jinzhou`

#### Scenario B: GROUP001 检装车通知单

Input:

```text
group_id: GROUP001
message_type: image
text: 检装车通知单
```

Expected:

- matched watch item: `检装车通知单`
- candidate projects include:
  - `zhongtang_special_steel`
  - `chaoyang_steel`
- candidate projects should not include `jilin_jingang_jinzhou` unless current SOP explicitly produced that watch item.

#### Scenario C: unknown group or unmatched text

Input:

```text
group_id: GROUP999
text: 出港计划通知单
```

Expected:

- no match
- clear reason

### 6.6 Allowed files

Implementation should be limited to:

```text
src/ops_hub/sop/monitoring_plan_matcher.py
src/ops_hub/sop/monitoring_plan_preview.py
```

Tests:

```text
tests/functional/test_monitoring_plan_matcher.py
```

Reports:

```text
reports/message_lifecycle_matcher_r8_20260525.md
reports/github_audit_message_lifecycle_matcher_r8_20260525.md
```

Order update:

```text
orders/sop_real_sop_fixture_topology_order_20260525.md
```

### 6.7 Tests to run

Run:

```bash
pytest tests/functional/test_monitoring_plan_matcher.py -v
pytest tests/functional -v
```

Expected result:

```text
all functional tests pass except intentional source supervision skip
```

### 6.8 Report requirement

Add report:

```text
reports/message_lifecycle_matcher_r8_20260525.md
```

The report must include:

- what was implemented;
- the three tested message scenarios;
- generated matches;
- explicit note that no real wx-ops-agent, DB, runtime, asset movement, OCR, or report sending was added;
- next planned round R9: raw asset bundle registration protocol.

### 6.9 Commit requirements

Commit message:

```text
feat: add monitoring plan message matcher
```

Push to:

```text
origin codex/sop-real-sop-topology-audit-20260525
```

## 7. Hermes response format

After completion, reply:

```text
Execution Result

branch: codex/sop-real-sop-topology-audit-20260525
commit: <commit sha>
PR: none
order: orders/sop_real_sop_fixture_topology_order_20260525.md
report: reports/message_lifecycle_matcher_r8_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command -> result>

summary:
- <what matching behavior was implemented>
- <ordinary freight scenarios covered>
- <what was not implemented>
- <next recommended order/update>
```

## 8. Forbidden actions

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
- over-test exact final business correctness;
- merge this branch.

## 9. Current round: R9 raw asset bundle registration protocol

R9 should define and test a raw asset bundle registration protocol that answers:

```text
Where is the raw image path registered?
Where is the OCR JSON path registered?
Where is the message metadata path registered?
How does MessageEvent bind to these assets?
```

### 9.1 Purpose

Keep this round local and functional-test based. Do not add runtime, database writes, WeChat integration, 95306, OCR execution, report sending, or asset moving/copying.

### 9.2 Required data structures

Use minimal dataclasses:

```text
MessageEvent
RawAssetBundle
```

Suggested fields:

```text
MessageEvent
- message_id
- group_id
- source_agent
- received_at
- message_type
- text
- raw_asset_bundle

RawAssetBundle
- message_id
- group_id
- source_agent
- received_at
- raw_image_path
- ocr_json_path
- message_metadata_path
- text
- extraction_kind
- registration_status
- warnings
```

The registration protocol should make incomplete asset sets explicit instead of crashing.

### 9.3 Required behavior

1. Register a raw image path, OCR JSON path, and message metadata path on the bundle.
2. Bind the bundle to the message event.
3. Preserve text-only events with metadata only.
4. Return a clear warning / incomplete status when required paths are missing.
5. Keep everything in local Python objects; no persistent storage.

### 9.4 Test scenarios

At minimum, add functional tests for:

#### Scenario A: image message with raw image + OCR JSON + metadata

Expected:

- bundle carries all three paths;
- event is bound to the bundle;
- status is complete;
- no crash.

#### Scenario B: text message with metadata path only

Expected:

- bundle carries metadata path only;
- event is bound to the bundle;
- text remains available;
- status is complete or explicitly acceptable for text-only registration.

#### Scenario C: missing asset path

Expected:

- no crash;
- clear warning;
- incomplete status.

### 9.5 Allowed files

Implementation should be limited to:

```text
src/ops_hub/sop/monitoring_plan_matcher.py
src/ops_hub/sop/raw_asset_bundle.py
```

Tests:

```text
tests/functional/test_raw_asset_bundle_registration.py
```

Reports:

```text
reports/raw_asset_bundle_r9_20260525.md
reports/github_audit_raw_asset_bundle_r9_20260525.md
```

### 9.6 Tests to run

Run:

```bash
pytest tests/functional -v
```

Expected result:

```text
all functional tests pass except the intentional source supervision skip
```

### 9.7 Report requirement

Add report:

```text
reports/raw_asset_bundle_r9_20260525.md
```

The report must include:

- what was implemented;
- the three test scenarios;
- how the bundle binds to MessageEvent;
- explicit note that no real wx-ops-agent, DB, runtime, asset movement, OCR, or report sending was added;
- next planned round R10.

### 9.8 Commit requirements

Commit message:

```text
feat: add raw asset bundle registration protocol
```

Push to:

```text
origin codex/sop-real-sop-topology-audit-20260525
```

### 9.9 Hermes response format

After completion, reply:

```text
Execution Result

branch: codex/sop-real-sop-topology-audit-20260525
commit: <commit sha>
PR: none
order: orders/sop_real_sop_fixture_topology_order_20260525.md
report: reports/raw_asset_bundle_r9_20260525.md
modified_files:
- <file>
new_files:
- <file>
git_status: <clean or summary>

tests:
- <command -> result>

summary:
- <what was implemented>
- <ordinary freight scope impact>
- <what was not implemented>
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
- call external services;
- use OCR or AI extraction;
- move/copy raw assets;
- send reports;
- over-test exact final business correctness;
- merge this branch.
