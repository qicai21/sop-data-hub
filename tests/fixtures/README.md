# Test fixtures (portable)

Deterministic inputs for unit/functional/replay tests. No production DB paths,
cookies, or passwords.

## Layout (growing)

| Path | Purpose |
|---|---|
| `rail_windows/` | 95306 window JSON for replay |
| `inspection/` | candidate / car-number snapshots |
| `messages/` | text_router inputs |
| `golden/` | expected outputs |

## Anonymization

Prefer stable pseudonyms (same real car → same fake car across files). Keep
structure and counts. Real ydid/box/car only when privately required and
confirmed — see the portable test gate issue §5.4.

## Layout (Phase 3)

| Path | Purpose |
|---|---|
| `rail_windows/jilin_lanqi_50_wagons.json` | Jilin 蓝鳍 50-car e2e rail + lot plans |
| `inspection/window_recover_anchor_old_ticket.json` | #144 window anchor vs old ticket |
| `jiusan/` | bulk grain cars / container type samples (merged from test-plans) |
| `FEE_TEST_BOUNDARY.md` | SOP vs fee_manager fee test ownership |

Load via `tests.support.replay.load_replay("…")`.
