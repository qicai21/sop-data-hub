# Tests — portable gate

Work order (archived): `docs/issues/archived/2026-08-08-工单-测试体系重构-双端开发与可移植门禁.md`.

## Commands

```bash
# Portable gate (dev Mac + runtime Mac after pull) — same on both machines
make test

make test-unit
make test-functional

# Machine-local readonly only (optional; not a release pass)
make test-live-readonly

# Runtime machine release checks (fail if 95306 readonly DB missing, etc.)
make smoke-runtime
```

### Dual-machine flow

```text
Dev Mac:  edit → make test → commit → push (after user auth)
Runtime:  git pull → make test → make smoke-runtime → kickstart daemons (START-HERE §4)
```

Optional pre-push hook: `ln -sf ../../scripts/git-hooks/pre-push .git/hooks/pre-push`

CI (GitHub Actions): `.github/workflows/test.yml` runs unit+functional on Python **3.12** and **3.14**.

## Layers

| Layer | Path | Notes |
|---|---|---|
| unit | `tests/unit/` | Fast, pure/single-module |
| functional | `tests/functional/` | Cross-module + temp DBs + fixtures |
| live | `tests/live/` | Requires `SOP_TEST_LIVE=1`; readonly only |

`make test` runs **only** `tests/unit` + `tests/functional` (portable gate).

## Safety rails (always on)

- WeChat `send_to_wechat` stubbed
- Ansteel `login` / `upload_and_verify` blocked
- Opening repo `data/sop_agent.db` fails the test (no write-prod switch)

## Adding tests

1. Prefer production schema via `sop_db` fixture / `tests.support.db.init_sop_db`.
2. Put machine-specific path assertions in `tests/live/`.
3. Accident regressions keep issue IDs in docstrings; do not delete as “duplicate”
   without same-entry + same-assert dominance proof.
