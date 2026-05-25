# SOP Data Hub Runtime Re-architecture Plan

Date: 2026-05-25
Branch: `codex/sop-data-hub-runtime-plan-20260525`
Scope: architecture plan + functional tests first

## 1. Core architecture decision

`ops-data-hub` should evolve into `SOP data hub`.

The important change is not only that it becomes a long-running service. The core change is that it becomes the business orchestration center that reads every project SOP, compiles monitoring requirements, coordinates source systems, and advances SOP runtime state.

Current situation:

```text
wx-ops-agent      = long-running WeChat capture service
rail95306-sync    = long-running railway fact sync service
ops-data-hub      = mostly static scripts / libraries imported by other modules
```

Target situation:

```text
SOP data hub runtime
  ├─ loads project SOPs
  ├─ compiles channel monitoring plans
  ├─ publishes watch plans to wx-ops-agent / future agents
  ├─ observes rail95306-sync facts
  ├─ routes channel events back to project SOP nodes
  ├─ advances SOP runtime state
  ├─ detects WAIT_METADATA / MANUAL_REVIEW / REPAIR candidates
  ├─ generates report tasks
  └─ refreshes dashboards
```

## 2. New central concept: SOP Monitoring Plan Compiler

Each project has its own SOP. However, actual source systems operate by channel.

Examples:

- 中唐特钢 SOP requires WeChat group 1 to watch:
  - 出港计划通知单
  - 检装车通知单

- 朝阳钢铁 SOP requires:
  - WeChat group 1: 出港计划通知单, 检装车通知单
  - WeChat group 3: 出港计划通知单, 文字放货消息

- 九三 SOP may require:
  - WeChat group 2: 到站消息
  - rail95306 account: shipment status facts

The compiler must turn project-level SOP requirements into channel-level monitoring plans.

Project view:

```text
Project SOP -> project nodes -> required channel inputs
```

Execution view:

```text
Channel / group / account -> watch items -> candidate projects -> target SOP nodes
```

## 3. Required data model

### 3.1 Project SOP

A project SOP is the business-facing definition.

```yaml
project_id: chaoyang_steel
project_name: 朝阳钢铁
sop_nodes:
  - node_id: departure_plan_notice
    node_name: 出港计划通知单
    monitoring:
      - channel: wechat
        group_id: group_1
        group_name: 微信1号群
        input_type: document
        document_type: 出港计划通知单

      - channel: wechat
        group_id: group_3
        group_name: 微信3号群
        input_type: document
        document_type: 出港计划通知单
```

### 3.2 Monitoring requirement

A monitoring requirement is the normalized form extracted from a project SOP.

```json
{
  "project_id": "chaoyang_steel",
  "sop_node_id": "departure_plan_notice",
  "channel": "wechat",
  "group_id": "group_3",
  "input_type": "document",
  "document_type": "出港计划通知单",
  "required": true
}
```

### 3.3 Channel monitoring plan

A channel monitoring plan is the compiled execution-facing output.

```yaml
wechat_monitoring_plan:
  group_3:
    group_name: 微信3号群
    watch_items:
      - input_type: document
        document_type: 出港计划通知单
        candidate_projects:
          - chaoyang_steel
        target_sop_nodes:
          chaoyang_steel:
            - departure_plan_notice
```

### 3.4 Runtime watch task

A runtime watch task is what a source connector such as `wx-ops-agent` can execute.

```json
{
  "watch_task_id": "wechat_group_3_departure_plan_notice",
  "channel": "wechat",
  "group_id": "group_3",
  "detect": {
    "message_types": ["image", "file", "text"],
    "document_types": ["出港计划通知单"],
    "text_patterns": []
  },
  "callback": {
    "target": "sop-data-hub",
    "event_type": "channel_event"
  }
}
```

## 4. Module boundary

### 4.1 wx-ops-agent

Should do:

- maintain WeChat login and capture;
- read compiled group monitoring plans;
- watch group messages, images, files, and text;
- emit channel events back to SOP data hub.

Should not do:

- understand project SOPs;
- decide final project ownership;
- advance SOP state;
- generate business reports;
- directly import SOP business logic.

### 4.2 rail95306-sync

Should do:

- maintain 95306 login and keepalive;
- sync railway facts;
- expose database/API views for shipment facts;
- report account and sync health.

Should not do:

- decide project SOP progression;
- generate business reports;
- own WeChat events;
- become the orchestration layer.

### 4.3 SOP data hub

Should do:

- load all project SOPs;
- compile monitoring plans;
- publish or export source watch plans;
- receive channel events;
- attribute events to candidate projects;
- route events to target SOP nodes;
- query rail95306-sync facts;
- drive state transitions;
- detect missing metadata and repair candidates;
- create report tasks and dashboard refresh jobs.

## 5. Runtime phases

### Phase 1: test-first planning branch

This branch adds:

1. this architecture document;
2. a functional test for `SopMonitoringPlanCompiler`;
3. a placeholder functional test for SOP data hub supervising `wx-ops-agent` and `rail95306-sync`.

No runtime service should be implemented in this branch.

### Phase 2: compiler implementation

Implement:

```text
ops_hub.sop.monitoring_plan_compiler.SopMonitoringPlanCompiler
```

Minimum API:

```python
compiler = SopMonitoringPlanCompiler()
plan = compiler.compile(project_sops)
```

The compiler must:

- group by channel and group/account/source id;
- deduplicate identical watch items within one group;
- preserve all candidate projects;
- preserve `target_sop_nodes` mapping;
- produce output suitable for source agents.

### Phase 3: source plan publisher

Add export/publish functions for:

```text
wechat_monitoring_plan -> wx-ops-agent watch config
rail95306_monitoring_plan -> rail95306-sync observation config / query scope
```

### Phase 4: SOP runtime daemon

Add a runtime daemon:

```bash
python -m ops_hub.runtime.daemon --once
python -m ops_hub.runtime.daemon --loop --interval 30
```

First version should be audit-only:

- read source health;
- read channel events;
- read SOP state;
- read 95306 facts;
- output runtime cycle audit;
- do not mutate business tables until a separate implementation order.

## 6. Functional tests introduced by this branch

### 6.1 SOP Monitoring Plan Compiler functional test

File:

```text
tests/functional/test_sop_monitoring_plan_compiler.py
```

Purpose:

- prove that multiple project SOPs compile into a group-level WeChat monitoring plan;
- prove that duplicate watch items are merged;
- prove that candidate project and target SOP node mappings are preserved.

### 6.2 SOP Data Hub supervises source modules placeholder test

File:

```text
tests/functional/test_sop_data_hub_source_supervision.py
```

Purpose:

- reserve the acceptance shape for verifying that SOP data hub can drive or monitor `wx-ops-agent` and `rail95306-sync`;
- keep the test skipped until runtime adapter contracts exist.

## 7. Acceptance criteria for this branch

- Architecture document exists.
- Compiler functional test exists and expresses the intended behavior.
- Source supervision placeholder test exists and is explicitly skipped.
- No database schema changes.
- No wx-ops-agent changes.
- No rail95306-sync changes.
- No runtime daemon implementation in this branch.
