# Tests — portable gate

Work order: `docs/issues/2026-08-08-工单-测试体系重构-双端开发与可移植门禁.md`.

## Commands

```bash
# Portable gate (dev Mac + runtime Mac after pull)
make test

make test-unit
make test-functional

# Machine-local readonly only (optional; not a release pass)
make test-live-readonly

# Runtime machine release checks (fail if 95306 readonly DB missing, etc.)
make smoke-runtime
```

## Layers

| Layer | Path | Notes |
|---|---|---|
| unit | `tests/unit/` | Fast, no network, tmp DBs |
| functional | `tests/functional/` + legacy `tests/test_*.py` / `tests/functional/test_*.py` during migration | Cross-module + fixtures |
| live | `tests/live/` | Requires `SOP_TEST_LIVE=1`; readonly only |

**Migration note (Phase 0–2):** `make test` currently runs the whole `tests/` tree
except `tests/live` and `tests/support`, so existing files keep working while we
`git mv` into `unit/` / `functional/`. Target end-state: only `tests/unit` +
`tests/functional`.

## Safety rails (always on)

- WeChat `send_to_wechat` stubbed
- Ansteel `login` / `upload_and_verify` blocked
- Opening repo `data/sop_agent.db` fails the test (no write-prod switch)

## Adding tests

1. Prefer production schema via `sop_db` fixture / `tests.support.db.init_sop_db`.
2. Put machine-specific path assertions in `tests/live/`.
3. Accident regressions keep issue IDs in docstrings; do not delete as “duplicate”
   without same-entry + same-assert dominance proof.
