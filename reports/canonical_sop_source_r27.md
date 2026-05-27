# R27: Canonical SOP Source Migration

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Problem

| Before | After |
|--------|-------|
| `business-system-docs/test-plan/fixtures/project_sops/*.yaml` | `config/project_sops/*.yaml` |
| Directory NOT git-tracked | **In-repo, git-tracked** |
| 4 copies across repos | **1 canonical source** |
| `project_id` values inconsistent (_baseline suffix) | Fixed: `chaoyang_steel`, `jiusan`, `zhongtang_special_steel` |

## Changes

### 1. SOP files — copied + renamed + project_id fixed

| Original | New | project_id |
|----------|-----|------------|
| `zt_steel_baseline.yaml` | `zhongtang.yaml` | `zhongtang_special_steel` |
| `chaoyang_steel_baseline.yaml` | `chaoyang.yaml` | `chaoyang_steel` |
| `jilin_jingang_jinzhou_baseline.yaml` | `jilin_jingang.yaml` | `jilin_jingang_jinzhou` |
| `jiusan_soybean_baseline.yaml` | `jiusan.yaml` | `jiusan` |

Also fixed `group_name: "数据单发群-[GROUP013]"` → `"数据单发群"` across 3 YAMLs.

### 2. SOPWatcher — YAML loading

`SopRuntime.reload()` now detects format:
- `.yaml` files present → `_reload_from_yaml()`: `load_project_sop()` → `_project_sop_yaml_to_compiler_input()` → `SopMonitoringPlanCompiler`
- No `.yaml` → `_reload_from_md()`: `load_normalized_project_sops()` → existing flow

YAML→compiler converter: skips text watch-items without patterns (`match_departure_text_template`, `match_text_template`, `always`) — fallback matcher handles keyword routing.

### 3. run_live_service.py

`DEFAULT_FIXTURE_DIR` changed: `tests/fixtures/sops/` → `config/project_sops/`

### 4. --status enhancement

`sop_runtime.source_of_truth`: `"git"` (when path contains `config/project_sops`)

### 5. Tests updated

All 4 R26 tests now use YAML fixtures from `config/project_sops/`. Test assertions updated for YAML file names.

## Verification

```
python scripts/run_live_service.py --status

sop_runtime:
  source_of_truth: git
  sop_dir: .../sop-data-hub/config/project_sops
  loaded_projects: [chaoyang_steel, jilin_jingang_jinzhou, jiusan, zhongtang_special_steel]
  sop_hash: 2d81709dd88a136f

tests/functional/: 48 passed in 1.07s
```

## Modified Files

| File | Action |
|------|--------|
| `config/project_sops/zhongtang.yaml` | **新建** (from zt_steel_baseline.yaml) |
| `config/project_sops/chaoyang.yaml` | **新建** (from chaoyang_steel_baseline.yaml) |
| `config/project_sops/jilin_jingang.yaml` | **新建** (from jilin_jingang_jinzhou_baseline.yaml) |
| `config/project_sops/jiusan.yaml` | **新建** (from jiusan_soybean_baseline.yaml) |
| `src/ops_hub/sop/sop_watcher.py` | YAML loader + converter + source_of_truth |
| `scripts/run_live_service.py` | DEFAULT_FIXTURE_DIR → config/project_sops/ |
| `tests/functional/test_live_service_bootstrap.py` | 5 tests updated for YAML path |
