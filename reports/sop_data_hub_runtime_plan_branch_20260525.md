# SOP Data Hub Runtime Planning Branch Report

Date: 2026-05-25
Repository: `qicai21/ops-data-hub`
Branch: `codex/sop-data-hub-runtime-plan-20260525`

## 1. Purpose

This branch records the next architecture direction for evolving `ops-data-hub` into `SOP data hub`.

The branch is intentionally test-first and planning-first. It does not implement the runtime daemon or modify existing business tables.

## 2. New architecture direction

`SOP data hub` should become the central service that:

1. loads all project SOPs;
2. compiles project SOP monitoring requirements into channel-level monitoring plans;
3. publishes watch plans to source modules such as `wx-ops-agent`;
4. observes `rail95306-sync` as a railway fact source;
5. receives source events;
6. routes events back to candidate projects and target SOP nodes;
7. advances SOP runtime state;
8. detects missing metadata, manual review items, and repair candidates.

## 3. Files added

### Architecture document

```text
docs/architecture/sop_data_hub_runtime_rearchitecture_20260525.md
```

Defines:

- why `ops-data-hub` should evolve into `SOP data hub`;
- the `SOP Monitoring Plan Compiler` concept;
- the project-SOP to channel-plan compilation flow;
- boundaries for `wx-ops-agent`, `rail95306-sync`, and SOP data hub;
- the staged runtime daemon plan.

### Functional test: monitoring plan compiler

```text
tests/functional/test_sop_monitoring_plan_compiler.py
```

Defines the expected functional behavior:

- input: multiple project SOP dictionaries;
- output: `wechat_monitoring_plan` grouped by `group_id`;
- duplicate watch items are merged;
- candidate projects are preserved;
- target SOP node mappings are preserved;
- 九三 only appears in `group_2` and must not pollute `group_1` or `group_3`.

### Public contract stub

```text
src/ops_hub/sop/__init__.py
src/ops_hub/sop/monitoring_plan_compiler.py
```

The stub exists only to make the import contract explicit. `compile()` intentionally raises `NotImplementedError`.

### Placeholder functional test: source supervision

```text
tests/functional/test_sop_data_hub_source_supervision.py
```

This is an intentionally skipped future acceptance test. It reserves the requirement that SOP data hub should supervise, drive, or monitor:

- `wx-ops-agent` as WeChat capture source;
- `rail95306-sync` as railway fact source.

## 4. Expected test state

The compiler functional test is expected to fail until `SopMonitoringPlanCompiler.compile()` is implemented.

The source supervision test is expected to be skipped until runtime adapter contracts exist.

## 5. Explicit non-goals

This branch does not:

- rename the repository;
- implement runtime daemon;
- alter database schema;
- modify `wx-ops-agent`;
- modify `rail95306-sync`;
- delete or rewrite reconciler logic;
- change existing production business flows.

## 6. Recommended next order

Next implementation order should be:

```text
Implement SopMonitoringPlanCompiler to satisfy tests/functional/test_sop_monitoring_plan_compiler.py
```

Minimum implementation rules:

1. group by channel and group/source id;
2. deduplicate watch items in the same group;
3. preserve candidate projects in deterministic order;
4. preserve `target_sop_nodes` mapping;
5. preserve text patterns for text watch items;
6. return source-agent-facing monitoring plans.
