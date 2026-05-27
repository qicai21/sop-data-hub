# R21: Zombie DB cleanup + Canonical agent DB migration

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/ops-data-hub`

## Part 1: Zombie DB cleanup

### Deleted files

| File | Size | Location | Status |
|------|------|----------|--------|
| `ops_data_hub.db` | 0 bytes | `ops-data-hub/data/` | deleted |
| `rail95306.db` | 0 bytes | `ops-data-hub/data/` | deleted |
| `message_store.db` | 0 bytes | `wx-ops-agent/data/` | deleted |

All three were empty files, never referenced by any active code.

### Reference removal

Removed mentions from:

- `reports/data_persistence_topology_audit.md` — removed 3 table rows, updated zombie count → "已清理"
- `reports/runtime_boundary_audit_r13_5.md` — removed 4 list items referencing zombie paths
- `reports/real_sop_fixture_topology_audit_20260525.md` — updated DB path references

**Grep verification**: zero hits for `ops_data_hub.db`, `rail95306.db`, `message_store.db` outside test file and audit reports.

---

## Part 2: Agent DB migration

### Before → After

| Before | After |
|--------|-------|
| `ops-data-hub/data/agent.db` | `sop-data-hub/data/sop_agent.db` |
| `config` → `agent_db_path: wx-ops-agent/.../agent.db` | `config` → `agent_db_path: sop-data-hub/.../sop_agent.db` |
| Default path: `data/agent.db` | Default path: `data/sop_agent.db` |
| Test path: `data/agent_test.db` | Test path: `data/test_sop_agent.db` |

### Copy executed

```
ops-data-hub/data/agent.db (1.1 MB, 29 release_batches + 170 wagon_shipments)
  → sop-data-hub/data/sop_agent.db
```

### Auto-migration logic

Added `_migrate_legacy_db()` to `src/ops_hub/data_agent/db.py`:

- On first `open_db()` call, checks for legacy `data/agent.db` at same parent dir
- If legacy exists and `sop_agent.db` does NOT exist → `shutil.copy2()`
- Logs: `migrated legacy agent.db → <path> (<size> bytes)`
- Idempotent: if `sop_agent.db` already exists, does nothing

### Config update

`config/settings.yaml`:
```yaml
# Before
agent_db_path: /Users/qicai21/projects/repos/wx-ops-agent/data/agent.db

# After
# R21: canonical path → sop-data-hub/data/sop_agent.db
agent_db_path: /Users/qicai21/projects/repos/sop-data-hub/data/sop_agent.db
```

### Code updates

| File | Change |
|------|--------|
| `src/ops_hub/config.py` | Default `agent_db_path` → `data/sop_agent.db`; `test_agent_db_path` → `data/test_sop_agent.db` |
| `src/ops_hub/data_agent/db.py` | Added `_migrate_legacy_db()` + auto-migration on `open_db()`; default fallback → `sop_agent.db` |
| `tests/conftest.py` | `tmp_db` → `test_sop_agent.db` |
| `scripts/gen_jljg_excel.py` | DB path → `sop-data-hub/data/sop_agent.db` |
| `scripts/generate_dispatch_board_data.py` | Help text + example → `sop_agent.db` |
| `scripts/jiusan_board_generate.py` | `AGENT_DB` → `data/sop_agent.db`, comments updated |
| `dashboard/dispatch_board_schema.md` | Source DB path → `sop-data-hub/data/sop_agent.db` |
| `dashboard/dispatch_board_data.example.json` | Example path → `sop_agent.db` |
| `README.md` | Description → `data/sop_agent.db` |
| `tests/test_inspection_95306_reconciler.py` | Hardcoded path → `sop-data-hub/.../sop_agent.db` |
| `tests/test_runner_artifacts.py` | Tmp var names → `sop_agent.db` |
| `tests/test_image_ingestion_contract.py` | Tmp var → `prod_sop_agent.db` |
| `reports/real_sop_fixture_topology_audit_20260525.md` | Path references → `sop_agent.db` |

### Scope boundaries

- **No wx-ops-agent modification** — `wx-ops-agent/data/agent.db` untouched (wx-ops-agent's own operational DB)
- **No 95306_collection.sqlite3 modification** — not touched
- **No jiusan_cycle.db modification** — not touched
- **No business logic changes** — only path/default/auto-migration

## Test results

```
tests/functional/test_db_migration_cleanup_r21.py::test_zombie_db_files_deleted PASSED
tests/functional/test_db_migration_cleanup_r21.py::test_legacy_agent_db_auto_migrates_to_sop_agent_db PASSED
tests/functional/test_db_migration_cleanup_r21.py::test_no_zombie_db_names_in_code PASSED
tests/functional/test_db_migration_cleanup_r21.py::test_data_dir_exists_and_sop_agent_db_present PASSED
tests/functional/test_db_migration_cleanup_r21.py::test_default_db_path_uses_sop_agent_db PASSED

5 passed in 0.24s (R21)

All functional tests: 44 passed in 0.66s (no regressions)
```

## Files Changed

| Type | Count | Files |
|------|-------|-------|
| DB file copy | 1 | `sop-data-hub/data/sop_agent.db` (new) |
| DB file delete | 3 | zombie DBs removed |
| Source code | 3 | `config.py`, `db.py`, `config/settings.yaml` |
| Scripts | 3 | `gen_jljg_excel.py`, `generate_dispatch_board_data.py`, `jiusan_board_generate.py` |
| Tests | 4 | `conftest.py`, 3 existing test files |
| Docs/reports | 7 | README, dashboard schema, example, 3 audit reports |
| New test | 1 | `test_db_migration_cleanup_r21.py` (5 tests) |
| R21 report | 1 | This file |
