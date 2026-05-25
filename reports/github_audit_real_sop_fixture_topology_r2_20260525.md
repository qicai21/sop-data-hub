# GitHub Audit Summary — Real SOP Fixture Topology R2

- Repository: `qicai21/ops-data-hub`
- Branch: `codex/sop-real-sop-topology-audit-20260525`
- Order: `orders/sop_real_sop_fixture_topology_order_20260525.md`
- Report: `reports/real_sop_fixture_topology_r2_20260525.md`

## 1. Branch chain

- `92b7cc8` docs: audit real sop fixture topology
- `0d73716` docs: add real sop fixture topology order
- `088cc5f` docs: add sop runtime r4 audit
- `1b15917` test: add basic sop monitoring compiler coverage
- `4b09cdc` docs: update sop runtime order for R3 basic compiler tests

## 2. Files added in this round

- `tests/fixtures/sops/zhongtang_special_steel_sop.md`
- `tests/fixtures/sops/chaoyang_steel_sop.md`
- `tests/fixtures/sops/jilin_jingang_sop.md`
- `tests/fixtures/sops/jiusan_soybean_sop.md`
- `tests/functional/test_real_sop_fixture_contract.py`
- `reports/real_sop_fixture_topology_r2_20260525.md`

## 3. Files modified in this round

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `tests/functional/test_real_sop_fixture_contract.py`

## 4. Scope check

- Stayed inside `ops-data-hub`.
- Used `qicai21/prompts_and_reports` as read-only source of truth.
- Did not modify `prompts_and_reports`.
- Did not touch runtime, publisher, `wx-ops-agent`, or `rail95306-sync`.
- Did not modify database schema.

## 5. Validation

- `pytest tests/functional -v` -> `9 passed, 1 skipped`

## 6. Git state to verify after commit

- Run `git status --short --branch` after commit.
- The branch should be clean before final handoff.
