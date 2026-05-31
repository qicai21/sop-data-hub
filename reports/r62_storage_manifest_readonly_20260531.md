# R62: 只读图片/JSON 清查 manifest

**日期**: 2026-05-31  
**分支**: `codex/sop-real-sop-topology-audit-20260525`  
**repo**: `qicai21/sop-data-hub`

## 目标

只读扫描 `wechat_images` 下所有图片/JSON 文件，交叉对照 DB 记录，生成清查 manifest，为 R63 canonical 重新归档提供输入。

## 产出

| 文件 | 路径 |
|---|---|
| CSV | `runtime/storage_cleanup_manifest/image_storage_manifest.csv` |
| JSON | `runtime/storage_cleanup_manifest/image_storage_manifest.json` |
| Summary | `runtime/storage_cleanup_manifest/image_storage_manifest_summary.json` |
| 脚本 | `scripts/build_storage_manifest.py` |

## Summary

| 指标 | 值 |
|---|---|
| total_files | **3056** |
| image_count | 2160 |
| json_count | 895 |
| other_count | 1 |
| canonical_document_count | **438** |
| business_critical_count | **64** |
| missing_db_path_count | 2261 |
| duplicate_sha_count | **430** (476 duplicate files) |

## Suggested Action 分布

| action | count |
|---|---|
| trash | 2298 |
| move | 341 |
| review | 376 |
| keep | 41 |

## Source Area 分布

| area | count |
|---|---|
| internal (_status/_raw/_previews) | 1115 |
| legacy (other/unknown) | 1083 |
| raw (group directories) | 617 |
| unmatched | 116 |
| group_extractions | 89 |
| projects | 36 |

## Canonical Document 统计

| document_type | total | business_critical |
|---|---|---|
| 检装车通知单 | 342 | ~42 |
| 出港计划通知单 | 96 | ~14 |

## 抽样记录

### 出港计划通知单 (3)
1. `数据单发群-GROUP013/extractions/出港计划通知单/25_..._result.json` — critical JSON in extractions → move
2. `数据单发群-GROUP013/extractions/出港计划通知单/22_..._result.json` — non-critical JSON → trash
3. `数据单发群-GROUP013/出港计划通知单/51_...jpg` — image in raw dir → review

### 检装车通知单 (3)
1. `数据单发群-GROUP013/检装车通知单/48_...jpg` — already archived to project → **keep**
2. `数据单发群-GROUP013/检装车通知单/41_...jpg` — already archived → **keep**
3. `数据单发群-GROUP013/检装车通知单/23_...jpg` — not yet archived → **move**

### 普通照片 (3)
1. `照片-火车涂写mark/1106_...jpg` → move to business/general
2. `照片-火车涂写mark/974_...jpg` → move to business/general
3. `照片-火车涂写mark/1105_...jpg` → move to business/general

## 只读确认

- ✅ 无文件移动
- ✅ 无文件删除
- ✅ 无 DB 路径更新
- ✅ 无 OCR/VLM 调用
- ✅ 无外部上传
- ✅ 无微信发送

## live_service / cursor / message_inbox 状态

| 项 | 状态 |
|---|---|
| live_service alive | ✅ true (pid=72725) |
| cursor | ✅ 8 sources, latest_local_id=2337 |
| message_inbox | ✅ 2 rows (unchanged) |
| image_ingestion_audit | ✅ 339 rows (unchanged) |
| release_batches | ✅ 30 rows (unchanged) |

## Commit

TBD (待填入)
