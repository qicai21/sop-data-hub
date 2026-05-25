# Real SOP Fixture Topology Audit — R2

- Repository: `qicai21/ops-data-hub`
- Branch: `codex/sop-real-sop-topology-audit-20260525`
- Order: `orders/sop_real_sop_fixture_topology_order_20260525.md`
- Round: `R2`

## 1. What changed

- Copied the four confirmed real SOP markdown files from `qicai21/prompts_and_reports` into `tests/fixtures/sops/`.
- Added a fixture contract test that checks the copied SOPs preserve source contract fields.
- Updated the order file to mark the branch round as `R2` and record the current round task.

## 2. Source SOPs used as truth

- `sops/zhongtangtegang_sop.md` from `prompts_and_reports` branch `main`
- `sops/chaoyangsteel_sop.md` from `prompts_and_reports` branch `main`
- `sops/jiusan_soybean_sop.md` from `prompts_and_reports` branch `main`
- `sops/jilin_jingang_jinzhou_sop.md` from `prompts_and_reports` branch `jilin-jingang-jinzhou-sop-init-20260521`

## 3. New fixture paths

- `tests/fixtures/sops/zhongtang_special_steel_sop.md`
- `tests/fixtures/sops/chaoyang_steel_sop.md`
- `tests/fixtures/sops/jilin_jingang_sop.md`
- `tests/fixtures/sops/jiusan_soybean_sop.md`

## 4. Loader contract test

Added:

- `tests/functional/test_real_sop_fixture_contract.py`

Coverage:

- each fixture file exists;
- each fixture file is non-empty;
- each fixture preserves the expected project identity and key source contract markers;
- the fixture directory contains only the four expected markdown docs.

## 5. Validation

Command run:

```bash
pytest tests/functional -v
```

Result:

- `9 passed`
- `1 skipped`

Skipped test:

- `tests/functional/test_sop_data_hub_source_supervision.py` remains skipped because cross-module supervision adapters are still not designed.

## 6. Scope boundaries preserved

Not modified:

- `prompts_and_reports`
- runtime / publisher
- `wx-ops-agent`
- `rail95306-sync`
- database schema

## 7. Next recommended order

Add a minimal markdown fixture loader or normalization adapter that can turn `tests/fixtures/sops/*.md` into the structured `project_sops` contract expected by the monitoring plan compiler tests.
