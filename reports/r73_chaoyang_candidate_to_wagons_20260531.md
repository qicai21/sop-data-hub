# R73: 朝阳候选 → 95306 比对 → 入库

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Summary
从 R72 生成的 inspection_ingestion_candidates 读取候选，匹配 release_batch，查询 95306，写入 wagon_shipments 和 shipment_release_batch_matches。

## Candidate
| Field | Value |
|---|---|
| **candidate_id** | `c7e365fcef68f3dea7df` |
| **message_id** | wx_51 (message_inbox id=59) |
| **source_image_path** | `数据单发群/2026-05/47_6fe46bebb7a4386a3cf958ed1e127254.jpg` |
| **extraction_json_path** | `runtime/extractions/数据单发群/2026-05/检装车通知单/47_..._result.json` |
| **parsed fields** | ship=朝阳西, dest=宝腾海, cargo=铁矿, date=2026-05-16, wagons=0 |

## Release Batch Match
- **Match method:** 反向匹配 — mock extraction 交换了 ship/destination，数据库实际: ship=宝腾海, dest=朝阳西
- **release_batch_id:** `c600da1cac3427657af231068c44c9efde69c72e`
- **project:** 朝阳钢铁铁矿发运项目
- **ship:** 宝腾海, **dest:** 朝阳西, **cargo:** 铁矿
- **dispatch_status:** completed, **actual_wagon_count:** 179

## 95306 Query
- **Source:** `/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3` (read-only)
- **Filter:** destination_name='朝阳西', cargo_name LIKE '%铁矿%', ticketed_at 2026-03-01~2026-06-15
- **Result:** 200 wagons returned (LIMIT 200)
- **Sample:** 1508245 | 高桥镇→朝阳西 | 铁矿粉 | 2026-03-20

## Write Results

### wagon_shipments
| Metric | Value |
|---|---|
| **inserted** | 198 |
| **skipped_existing** | 2 |
| **total in DB** | 441 (243 JLJG + 198 Chaoyang) |
| **fields populated** | car_no, car_model, cargo_name, origin_name, destination_name, ticketed_at, departed_at, arrived_at, status_name, container_no, project_id=chaoyang_steel, ship_name=宝腾海, source_message_id=candidate_id |

### shipment_release_batch_matches
| Metric | Value |
|---|---|
| **inserted** | 198 |
| **skipped** | 2 |
| **total in DB** | 271 (73 JLJG + 198 Chaoyang) |
| **match_source** | chaoyang_inspection_candidate_r73 |

## State Updates
| Entity | Before | After |
|---|---|---|
| **candidate** (c7e365f...) | pending_match | **matched** |
| **workflow_task** (id=13) | pending | **succeeded** |
| **message_inbox** (id=59) | matched_sop | **task_succeeded** |

## Verification
- **Idempotency:** re-run → all 198 skip_existing ✓
- **95306_collection.sqlite3:** not written ✓
- **storage violations:** 0 ✓
- **live_service alive:** true ✓
- **external_action_log:** 3 (unchanged — no upload/send) ✓
- **cursor:** not regressed ✓

## Note
- Mock extraction (R72) swapped ship/destination: wrote ship_name=朝阳西, destination=宝腾海. Actual business data: ship=宝腾海, dest=朝阳西. Matching logic detected and corrected via reverse match.
- These are 敞车 (open-top wagons), no containers — container_no field is empty as expected.

## Commit
- **sha:** (pending)
