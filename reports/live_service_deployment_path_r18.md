# R18: Fix live service deployment path and startup validation

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/ops-data-hub`

## Problem

Live service acceptance failed:

1. `ps aux | grep run_live_service` showed only the grep process — no real python process.
2. `runtime/live_service.log` only contained `poll complete processed=0 seen=0`.
3. `runtime/events` was empty.
4. `runtime/dashboard_intents` did not have 木森17.
5. Runtime output was landing under `ops-data-hub/runtime/` instead of the canonical `sop-data-hub/runtime/`.

## Root Cause

- `DEFAULT_RUNTIME_ROOT` was `REPO_ROOT / "runtime"` where `REPO_ROOT` resolved to the checkout dir (`ops-data-hub`), not the canonical target (`sop-data-hub`).
- No PID file was written, making it impossible to verify liveness.
- No startup validation existed — the service could silently fail to start.
- No status command existed to query service health.

## Changes

### `scripts/run_live_service.py`

1. **Canonical paths (R18.1–R18.3):**
   - `CANONICAL_REPO_ROOT = ~/projects/repos/sop-data-hub`
   - `DEFAULT_RUNTIME_ROOT = ~/projects/repos/sop-data-hub/runtime/`
   - `DEFAULT_CHAT_RECORDS_ROOT = ~/projects/repos/wx-ops-agent/data/chat_records`

2. **PID file (R18.4):**
   - `_write_pid_file()` writes `os.getpid()` to `runtime/live_service.pid` on startup.
   - `_read_pid_file()` reads it back for status checks.

3. **Startup validation (R18.5):**
   - `_validate_startup()` confirms:
     - Current PID is alive (`os.kill(pid, 0)`)
     - `chat_records_root` exists and is a directory
     - `runtime/events`, `runtime/dashboard_intents`, `runtime/dashboard_state` are created
   - Raises on failure before poll loop starts.

4. **`--status` command (R18.6):**
   - `python scripts/run_live_service.py --status`
   - Outputs JSON:
     ```json
     {
       "pid": 12345,
       "alive": true,
       "runtime_root": "/Users/.../sop-data-hub/runtime",
       "chat_records_root": "/Users/.../wx-ops-agent/data/chat_records",
       "last_log_line": "..."
     }
     ```
   - When no PID file exists: `pid=null, alive=false`.

### `tests/functional/test_live_service_bootstrap.py`

Added 3 new tests (1 existing test updated):

| Test | What it covers |
|------|---------------|
| `test_run_live_service_bootstrap_once_writes_runtime_artifacts_and_pid_file` | `--once` works, PID file written (R18 coverage added) |
| `test_status_returns_alive_false_when_no_pid` | `--status` with no PID → `alive=false` |
| `test_runtime_root_uses_sop_data_hub_semantics` | `DEFAULT_RUNTIME_ROOT` → `~/projects/repos/sop-data-hub/runtime/` |
| `test_default_chat_records_root_points_to_wx_ops_agent` | `DEFAULT_CHAT_RECORDS_ROOT` → `~/projects/repos/wx-ops-agent/data/chat_records` |

## Test Results

```
tests/functional/test_live_service_bootstrap.py::test_run_live_service_bootstrap_once_writes_runtime_artifacts_and_pid_file PASSED
tests/functional/test_live_service_bootstrap.py::test_status_returns_alive_false_when_no_pid PASSED
tests/functional/test_live_service_bootstrap.py::test_runtime_root_uses_sop_data_hub_semantics PASSED
tests/functional/test_live_service_bootstrap.py::test_default_chat_records_root_points_to_wx_ops_agent PASSED

4 passed in 0.14s
```

## Scope Boundaries (R18.7–R18.11)

- **No new business features** — only path, PID, and startup validation changes.
- **No wx-ops-agent modifications** — read-only from `data/chat_records`.
- **No database writes** — all state stays in files under `runtime/`.
- **No 九三 handling** — out of scope.
- **No report sending** — report generated locally only.

## Files Changed

| File | Change |
|------|--------|
| `scripts/run_live_service.py` | Canonical paths, PID file, startup validation, `--status` command |
| `tests/functional/test_live_service_bootstrap.py` | 4 tests covering R18 requirements |
| `reports/live_service_deployment_path_r18.md` | This report |
