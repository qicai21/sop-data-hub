# R19: Debug live intake real appended WeChat messages

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/ops-data-hub`

## Problem

wx-ops-agent confirmed the following messages existed in chat records:

1. `铁晟业务工作群/2026-05.jsonl` — multiple 木森17 / 朝阳西 messages
2. `数据单发群-GROUP013/2026-05.jsonl` — seq=52: "朝阳西放货计划，木森17,新放货 5375 吨，总放货量 15975 吨"

But SOP Data Hub runtime never wrote:
- `runtime/events`
- `runtime/dashboard_intents`
- `runtime/dashboard_state`

Service log only showed `poll complete processed=0 seen=0`.

## Root Cause Investigation

### Issue 1 (primary): Off-by-one parent chain in `_default_wx_ops_agent_root()`

**File:** `src/ops_hub/sop/source_watcher.py`

```python
# BEFORE (broken):
return Path(__file__).resolve().parents[4].parent / "wx-ops-agent"
# Resolved to: /Users/qicai21/projects/wx-ops-agent  ← DOES NOT EXIST
```

The source watcher file sits at `repos/ops-data-hub/src/ops_hub/sop/source_watcher.py`:
- `parents[4]` = `repos/`
- `.parent` (the bug) = `projects/` ← one level too high
- `/ "wx-ops-agent"` = `projects/wx-ops-agent` ← wrong

**Impact:** `WxOpsSourceWatcher()` default chat_records_root resolved to a non-existent directory. `rglob("*.jsonl")` found zero files → 0 events → 0 processed.

**Fix:**
```python
# AFTER (fixed):
return Path(__file__).resolve().parents[4] / "wx-ops-agent"
# Resolved to: /Users/qicai21/projects/repos/wx-ops-agent  ← CORRECT
```

Removed the stray `.parent` — `parents[4]` already points to `repos/`.

### Issue 2: GROUP013 monitoring plan mismatch

The monitoring plan for GROUP013 only had one watch item with `candidate_projects: ["jiusan"]`. The `_group_plan_for_event()` function did exact-match only on `group_name`.

**Two sub-issues:**

1. **Group name mismatch:** The watcher derives `group_id` from `source_path.parent.name` which is `数据单发群-GROUP013` (directory name). The plan's `group_name` is `数据单发群` (without suffix). Exact match failed → `_group_plan_for_event` returned no plan → event rejected.

2. **Missing fallback alignment:** Even if the group plan was found, GROUP013 had no `_fallback_alignment_match` handler for 朝阳西/木森17 keywords. GROUP001 and GROUP003 had fallback handlers, but GROUP013 did not.

**Fix 2a:** Added substring matching in `_group_plan_for_event()` — if the plan's group_name is a substring of the event's group_name, it matches.

**Fix 2b:** Added GROUP013 fallback in `_fallback_alignment_match()`:
```python
elif group_id == "GROUP013":
    if any(token in event_text for token in ("朝阳西", "木森17")):
        project_id = "chaoyang_steel"
        anchor_text = "朝阳西木森17"
```

### Issue 3 (operational): No catch-up mechanism after path fix

After fixing the path bug, the service would reprocess ALL historical messages (6089 events found). Added `--sync-start-id` to skip already-processed messages by local_id/seq.

## Changes

### `src/ops_hub/sop/source_watcher.py`
- Fixed `_default_wx_ops_agent_root()`: removed stray `.parent` from the path computation.

### `src/ops_hub/sop/monitoring_plan_matcher.py`
- `_group_plan_for_event()`: added substring matching for group_name (handles `数据单发群-GROUP013` → `数据单发群`).
- `_fallback_alignment_match()`: added GROUP013 handler for `朝阳西`/`木森17` → `chaoyang_steel`.

### `scripts/run_live_service.py`
- Added `--sync-start-id` parameter: skip events with `local_id < sync_start_id`.
- `run_live_service()` and `run_once()` now accept and pass through `sync_start_id`.

### `tests/functional/test_live_service_bootstrap.py`
- `test_group013_seq52_chao_forge_message_routes_to_chaoyang_steel` — end-to-end replay of real seq=52 message.
- `test_sync_start_id_filters_lower_seqs` — verifies `--sync-start-id` filtering.
- `test_group013_group_name_substring_match` — verifies substring matching in `_group_plan_for_event`.
- `test_source_watcher_default_resolves_correctly` — verifies parent chain fix.

## Verification

### Real data dry-run (with --sync-start-id 52)

```
$ python scripts/run_live_service.py --once --runtime-root /tmp/r19_runtime --sync-start-id 52

processed event message_id=wx_52 group_id=数据单发群-GROUP013 payloads=1 states=1
```

Generated artifacts:
- `runtime/events/数据单发群-GROUP013/2026-05/wx_52.json` ✓
- `runtime/dashboard_intents/wx_52.json` → `project_id: "chaoyang_steel"` ✓
- `runtime/dashboard_state/wx_52.json` → `status: "active"` ✓

### Test results

```
tests/functional/test_live_service_bootstrap.py::test_run_live_service_bootstrap_once_writes_runtime_artifacts_and_pid_file PASSED
tests/functional/test_live_service_bootstrap.py::test_status_returns_alive_false_when_no_pid PASSED
tests/functional/test_live_service_bootstrap.py::test_runtime_root_uses_sop_data_hub_semantics PASSED
tests/functional/test_live_service_bootstrap.py::test_default_chat_records_root_points_to_wx_ops_agent PASSED
tests/functional/test_live_service_bootstrap.py::test_group013_seq52_chao_forge_message_routes_to_chaoyang_steel PASSED
tests/functional/test_live_service_bootstrap.py::test_sync_start_id_filters_lower_seqs PASSED
tests/functional/test_live_service_bootstrap.py::test_group013_group_name_substring_match PASSED
tests/functional/test_live_service_bootstrap.py::test_source_watcher_default_resolves_correctly PASSED

8 passed in 0.22s
```

## Scope Boundaries (R19.7–R19.11)

- **No wx-ops-agent modifications** — read-only from `data/chat_records`.
- **No database writes** — all state stays in `runtime/`.
- **No dashboard HTML changes** — out of scope.
- **No report sending** — report generated locally only.
- **No 九三 business logic** — 九三 remains in monitoring plan but not altered. GROUP013 fallback only routes 朝阳西/木森17 to chaoyang_steel.

## Files Changed

| File | Change |
|------|--------|
| `src/ops_hub/sop/source_watcher.py` | Fixed parent chain off-by-one |
| `src/ops_hub/sop/monitoring_plan_matcher.py` | Substring group_name matching + GROUP013 fallback |
| `scripts/run_live_service.py` | Added `--sync-start-id` parameter |
| `tests/functional/test_live_service_bootstrap.py` | +4 R19 tests (8 total) |
| `reports/live_intake_real_message_debug_r19.md` | This report |
