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
