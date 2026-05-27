# R22: Finalize SOP Data Hub base migration

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/ops-data-hub`
**Tag:** `sop-data-hub-foundation-v1`

## Part 1: Service shutdown

### Process audit

| Process | PID | Status |
|---------|-----|--------|
| `run_live_service --sync-start-id 52` | 67596 | stopped (from old ops-data-hub cwd) |
| watcher | — | no process found |
| live intake | — | no process found |

**Result**: all services stopped. `ps aux | grep run_live_service` returns no results.

---

## Part 2: Checkout state comparison

| Check | ops-data-hub (old) | sop-data-hub (canonical) |
|-------|-------------------|-------------------------|
| Branch | `codex/sop-real-sop-topology-audit-20260525` | same |
| HEAD | `201f340` (R21) | same |
| Pushed | ✓ | ✓ |
| Status | dirty (1M + 14??) | **clean** |

---

## Part 3: Old checkout deletion

```
rm -rf ~/projects/repos/ops-data-hub
```

Preconditions confirmed:
1. Current branch pushed to origin ✓
2. sop-data-hub at same commit, not behind ✓
3. Dirty items were uncommitted artifacts only (old reports), safe to discard

---

## Part 4: Canonical checkout verification

```
~/projects/repos/sop-data-hub
```

| Check | Result |
|-------|--------|
| `git status` | clean (no output) |
| `runtime/` gitignored | ✓ |
| `ops_data_hub.db` | not found |
| `rail95306.db` | not found |
| `message_store.db` | not found |
| `agent.db` (legacy) | not found |
| `sop_agent.db` | 1.1 MB present ✓ |
| `ops-data-hub` refs in code | 0 hits outside reports |

---

## Part 5: Foundation tag

```
git tag -a sop-data-hub-foundation-v1
  "SOP Data Hub base migration complete"
git push origin sop-data-hub-foundation-v1
```

Tag points to commit `201f340` (R21).

---

## Migration summary (R18–R22)

| R# | Scope |
|-----|-------|
| R18 | Live service deployment path fix |
| R19 | Live intake debug + parent chain + GROUP013 routing |
| R20 | Canonical repo checkout migration |
| R21 | Zombie DB cleanup + agent.db → sop_agent.db |
| R22 | Service shutdown + old checkout removal + foundation tag |

### Final state

| Before | After |
|--------|-------|
| 2 checkouts (ops-data-hub + sop-data-hub) | 1 checkout (sop-data-hub) |
| 3 zombie DBs (0 bytes each) | 0 zombie DBs |
| agent.db in ops-data-hub | sop_agent.db in sop-data-hub |
| Runtime in ops-data-hub/runtime | Runtime in sop-data-hub/runtime (gitignored) |
| No tag | `sop-data-hub-foundation-v1` |

### Scope boundaries

- **No new features** — foundation/infra only
- **No database schema changes**
- **No wx-ops-agent modification**
- **No long-running services started**
- **GitHub remote preserved** — only local checkout deleted
