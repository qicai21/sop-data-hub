# R73: 朝阳候选到入库

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Goal
将 R72 创建的 Chaoyang 检装车通知单 candidate 关联到 release_batch，查询 95306 数据，写入 wagon_shipments 和 shipment_release_batch_matches。

## Pipeline Flow
R72 candidate (`c7e365fcef68f3dea7df`) → release_batch match → 95306 query → wagon_shipments insert → shipment_release_batch_matches insert

### Release Batch Match
- **candidate:** ship=朝阳西, dest=宝腾海, cargo=铁矿 (mock extraction had swapped ship/dest)
- **actual batch:** 宝腾海/朝阳西/铁矿/lot01
- **release_batch_id:** `c600da1cac3427657af231068c44c9efde69c72e`
- **batch status:** completed, actual_wagon_count=179
- **project:** 朝阳钢铁铁矿发运项目

### 95306 Query
- **Rail DB:** `/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3`
- **Query:** destination_name='朝阳西', cargo_name LIKE '%铁矿%', ticketed_at 2026-03~06
- **Results:** 200 wagons (LIMIT 200)

## Results

### message_inbox
| Field | Value |
|---|---|
| **id** | 59 |
| **processing_status** | task_succeeded |
| **release_batch_id** | `c600da1cac3427657af231068c44c9efde69c72e` |

### inspection_ingestion_candidates
| Field | Value |
|---|---|
| **id** | `c7e365fcef68f3dea7df` |
| **candidate_status** | matched |
| **release_batch_id** | `c600da1cac...` |
| **wagon_count** | 200 |

### wagon_shipments
| Metric | Value |
|---|---|
| **inserted** | 198 |
| **skipped (duplicate)** | 2 |
| **total in DB** | 441 (243 R71 + 198 R73) |
| **for Chaoyang batch** | 198 |

### shipment_release_batch_matches
| Metric | Value |
|---|---|
| **inserted** | 198 |
| **skipped** | 2 |
| **total in DB** | 271 (73 R71 + 198 R73) |

### workflow_task_db
| Field | Value |
|---|---|
| **id** | 13 |
| **task_type** | chaoyang_inspection_candidate_match |
| **task_status** | succeeded |

## Verification

### Data Integrity
- **wagon_shipments:** 441 (198 new for Chaoyang) ✅
- **shipment_release_batch_matches:** 271 (198 new) ✅
- **95306_collection.sqlite3 in sop-data-hub:** not created ✅
- **Rail DB (rail95306-sync):** read-only query, not modified ✅

### System State
- **storage violations:** 0 ✅
- **live_service alive:** true ✅
- **No factory upload, no WeChat send** ✅

### Dedup Safety
- car_no-based dedup: 2 wagons already existed (from first-fail partial insert), correctly skipped
- release_batch_id + wagon_no UNIQUE not enforced at DB level but checked in code
- Re-running the script is idempotent (2nd run: 0 inserted, all skipped)

## Commands
```bash
PYTHONPATH=src /opt/homebrew/bin/python3.14 scripts/r73_chaoyang_candidate_to_wagons.py
```

## Commit
- **sha:** (pending)
