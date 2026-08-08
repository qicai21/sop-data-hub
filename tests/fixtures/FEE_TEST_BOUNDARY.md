# SOP vs fee_manager test boundary (Phase 3, 2026-08-08)

## Decision (D)

**Keep running** SOP fee-related portable tests until fee_manager fully owns the
same pure helpers. Do **not** skip them.

## SOP retains (transport + shared pure helpers still imported from sop_hub)

| Test | Why keep |
|---|---|
| `tests/unit/test_jiusan_bulk_fee_helpers.py` | Exercises `sop_hub.fees.jiusan_bulk` pure calc (track, billing weight, fee items). fee_manager has jiusan aux/receipt tests against its ledger, not a 1:1 import of these helpers. |
| `tests/functional/test_r82_contract_fee_schema.py` | **Transport schema contract**: `open_db` creates R82 tables/columns on SOP DB that fee_manager source-syncs from. Belongs in SOP. |

## fee_manager owns

Settlement plans, invoices, fee_batch authority, source sync from SOP, jiusan
receipt/aux ledger flows under `fee_manager/tests/`.

## Follow-up (not this phase)

If `sop_hub.fees.*` is later deleted or re-exported only from fee_manager, move
helper unit tests with the code and leave only R82/schema/sync-field contracts here.
