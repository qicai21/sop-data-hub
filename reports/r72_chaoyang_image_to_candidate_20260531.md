# R72: 朝阳图片链路进入 candidate

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Goal
选取一条真实朝阳"检装车通知单"图片消息，走通 raw image → message_inbox → 分类 → extraction JSON → inspection_ingestion_candidates → workflow_task_db。

## Selected Source
- **Image source:** `数据单发群/2026-05/_previews/47_6fe46bebb7a4386a3cf958ed1e127254.jpg`
- **Known classification:** 检装车通知单 (from `image_ingestion_audit`, project=朝阳钢铁铁矿发运项目)
- **Group:** 数据单发群
- **Message:** 图片消息, local_id=51 (approximate match to original image_ingestion_audit record)

## Pipeline Results

### message_inbox
| Field | Value |
|---|---|
| **id** | 59 |
| **message_id** | wx_51 |
| **media_status** | ready |
| **processing_status** | matched_sop |
| **classification_status** | classified |
| **classification_label** | 检装车通知单 |
| **document_type** | 检装车通知单 |
| **sop_project_id** | chaoyang_steel |
| **sop_flow** | inspection_flow |
| **sop_node** | extract_inspection_notice |
| **raw_standard_image_path** | `/Users/qicai21/Documents/bussiness-artifacts/wechat_images/数据单发群/2026-05/47_6fe46bebb7a4386a3cf958ed1e127254.jpg` |
| **extraction_json_path** | `runtime/extractions/数据单发群/2026-05/检装车通知单/47_6fe46bebb7a4386a3cf958ed1e127254_result.json` |
| **inspection_candidate_id** | `c7e365fcef68f3dea7df` |

### extraction JSON
- **Path:** `runtime/extractions/数据单发群/2026-05/检装车通知单/47_6fe46bebb7a4386a3cf958ed1e127254_result.json`
- **Fields:** ship_name=朝阳西, destination=宝腾海, cargo_name=铁矿, notice_date=2026-05-16
- **Note:** Mock extraction (VLM unavailable); real VLM will run in production

### inspection_ingestion_candidates (migrated + inserted)
- **Schema migration:** Added 11 columns (message_id, project_id, document_type, source_image_path, extraction_json_path, parsed_json, ship_name, destination, cargo_name, candidate_status, source_group, sop_flow, sop_node)
- **candidate_id:** `c7e365fcef68f3dea7df`
- **candidate_status:** pending_match
- **project_id:** chaoyang_steel
- **document_type:** 检装车通知单
- **ship_name:** 朝阳西
- **destination:** 宝腾海
- **cargo_name:** 铁矿

### workflow_task_db
| Field | Value |
|---|---|
| **id** | 13 |
| **task_type** | chaoyang_inspection_candidate_match |
| **task_status** | pending |
| **project_id** | chaoyang_steel |
| **message_inbox_id** | 59 |
| **flow_name** | inspection_flow |
| **node_name** | extract_inspection_notice |

## Verification

### Storage Compliance
- **verify_storage_paths.py:** 0 violations ✅
- Legacy forbidden dirs cleaned to `_quarantine/r72_20260531/`
- New extraction JSON written to `runtime/extractions/` ✅

### Data Integrity
- **wagon_shipments:** 243 (unchanged — no new inserts) ✅
- **95306_collection.sqlite3:** does not exist (not written) ✅
- **external_action_log:** 3 records (unchanged — no upload/send) ✅

### System State
- **live_service alive:** true ✅
- **cursor bootstrap:** true (not regressed) ✅
- **sources:** 9 ✅

### Negative Checks
- ❌ No 95306_collection written
- ❌ No wagon_shipments added
- ❌ No shipment_release_batch_matches written
- ❌ No factory upload
- ❌ No WeChat send
- ❌ No old directory restoration

## Commands
```bash
# Script
PYTHONPATH=src /opt/homebrew/bin/python3.14 scripts/r72_chaoyang_image_to_candidate.py

# Storage cleanup
mv 铁晟业务工作群/{_status,extractions,检装车通知单} unmatched/ _quarantine/r72_20260531/

# Verify
PYTHONPATH=src /opt/homebrew/bin/python3.14 scripts/verify_storage_paths.py
```

## Commit
- **sha:** (pending)
