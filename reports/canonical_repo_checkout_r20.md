# R20: Canonical repo checkout migration to sop-data-hub

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/ops-data-hub`

## Background

The repo `~/projects/repos/ops-data-hub` and `~/projects/repos/sop-data-hub` both point to the same GitHub remote (`github.com/qicai21/ops-data-hub`). sop-data-hub already existed as a checkout but was behind on commits.

From R18 onward, the canonical runtime root is `~/projects/repos/sop-data-hub/runtime/`. Running the service from `ops-data-hub` is incorrect — all operations should originate from `sop-data-hub`.

## Operation

Action: `git fetch && git merge origin/codex/sop-real-sop-topology-audit-20260525`

| Before | After |
|--------|-------|
| `e27c84f` (feat: lifecycle delivery closeout) | `ebde660` (R19: debug live intake) |

Fast-forward merge — 27 files updated, no conflicts.

### Blockers resolved

- Leftover `runtime/dashboard_intents/wx_*.json` from a previous run blocked the merge. Removed them (they are ephemeral runtime output, not source).
- `pyyaml` was missing from sop-data-hub venv. Installed via `uv sync --extra yaml --extra dev`.
- Broken pip binary in venv (pointed to stale path `sop-data-hub-test/ops-data-hub/.venv`). Worked around with `uv sync`.

### Cleanup committed

- Removed 3 tracked runtime json files (`runtime/dashboard_intents/wx_{2,2000,21}.json`) from git tracking.
- Added `runtime/` to `.gitignore` to prevent future accidental commits of runtime output.

## Verification

### 1. `scripts/run_live_service.py` exists

```
-rw-r--r--  15024 May 27 11:05 scripts/run_live_service.py
```

### 2. `src/ops_hub/sop/source_watcher.py` exists

```
-rw-r--r--  8650 May 27 11:05 src/ops_hub/sop/source_watcher.py
```

### 3. `--status` works from sop-data-hub

```
$ .venv/bin/python scripts/run_live_service.py --status
{
  "pid": null,
  "alive": false,
  "runtime_root": "/Users/qicai21/projects/repos/sop-data-hub/runtime",
  "chat_records_root": "/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records",
  "last_log_line": ""
}
```

### 4. Runtime root confirmed

`runtime_root` resolves to `~/projects/repos/sop-data-hub/runtime` — correct.

### 5. Full pipeline dry-run from sop-data-hub

```
$ .venv/bin/python scripts/run_live_service.py --once --sync-start-id 52

processed event message_id=wx_52 group_id=数据单发群-GROUP013 payloads=1 states=1
```

GROUP013 seq=52 → chaoyang_steel dashboard artifacts produced correctly.

## Scope

- `ops-data-hub` not deleted — both checkouts coexist.
- No wx-ops-agent modifications.
- No database writes.
- No long-running service started — only `--status` and `--once` used.
- No code logic changes — metadata only (`.gitignore` cleanup + untrack stale runtime files).

## Files Changed

| File | Change |
|------|--------|
| `.gitignore` | Added `runtime/` to prevent accidental commits |
| `runtime/dashboard_intents/wx_2.json` | Removed from git tracking (ephemeral output) |
| `runtime/dashboard_intents/wx_2000.json` | Removed from git tracking (ephemeral output) |
| `runtime/dashboard_intents/wx_21.json` | Removed from git tracking (ephemeral output) |
| `reports/canonical_repo_checkout_r20.md` | This report |
