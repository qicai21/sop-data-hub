# R63: 重点单据 canonical 重新归档

**日期**: 2026-05-31
**分支**: `codex/sop-real-sop-topology-audit-20260525`
**repo**: `qicai21/sop-data-hub`

## 目标

从 R62 manifest 中筛选 business_critical canonical documents，hardlink 到 storage_policy.yaml 定义的 canonical project archive 路径，更新 sop_agent.db。

## 产出

| 文件 | 路径 |
|---|---|
| CSV | `runtime/storage_canonical_archive_r63/r63_canonical_archive_result.csv` |
| JSON | `runtime/storage_canonical_archive_r63/r63_canonical_archive_result.json` |
| Summary | `runtime/storage_canonical_archive_r63/r63_canonical_archive_summary.json` |
| 脚本 | `scripts/archive_canonical_documents.py` |

## Dry-run 结果

| 指标 | 值 |
|---|---|
| target_processed | 47 |
| archived (planned) | 8 |
| kept (verified) | 39 |
| review | 0 |
| failed | 0 |

## Apply 结果

| 指标 | 值 |
|---|---|
| **archived_count** | **8** |
| **kept_count** | **39** |
| **review_count** | **0** |
| **failed_count** | **0** |
| **db_update_count** | **5** (image_ingestion_audit 记录更新) |
| **message_inbox_update_count** | 0 (无匹配记录) |
| **hardlink_count** | **8** |
| **copy_count** | **0** |
| incomplete_context | 4 (19/21 检装车通知单，candidates 未写入 DB) |
| fallback_message_id | 44 (message_id 缺失时使用 file stem) |

## 归档详情

### 出港计划通知单 (2 files → 1 pair)
| source | canonical path |
|---|---|
| GROUP013/出港计划通知单/25_...jpg | `business/projects/中唐特钢.../沙子/鞍子河/lot01/release/2026-05-13/25_...jpg` |
| GROUP013/extractions/出港计划通知单/25_...json | `business/projects/中唐特钢.../沙子/鞍子河/lot01/release/2026-05-13/25_...json` |

### 检装车通知单 (6 files → 3 pairs)
| source | canonical path | context |
|---|---|---|
| GROUP013/检装车通知单/23_...jpg | `.../汐子/丰收散运/lot01/inspection/2026-05-13/23_...jpg` | complete |
| GROUP013/extractions/检装车通知单/23_...json | same dir | complete |
| GROUP013/检装车通知单/19_...jpg | `.../unknown/unknown/lotunknown/inspection/2026-05-12/19_...jpg` | incomplete |
| GROUP013/extractions/检装车通知单/19_...json | same dir | incomplete |
| GROUP013/检装车通知单/21_...jpg | `.../unknown/unknown/lotunknown/inspection/2026-05-13/21_...jpg` | incomplete |
| GROUP013/extractions/检装车通知单/21_...json | same dir | incomplete |

## 抽样核验

### 出港计划通知单 (3)
1. `25_9bbf45...jpg` → hardlink ✓ (inode match: 16487316)
2. `25_9bbf45...json` → hardlink ✓ (inode match: 16487339)

### 检装车通知单 (3)
1. `23_faaa7f...jpg` → hardlink ✓ (inode match: 16486784)
2. `19_f46f58fa...jpg` → hardlink ✓ (inode match: 16276685)
3. `21_542f2620...jpg` → hardlink ✓ (inode match: 16452727)

## 确认项

| 项 | 状态 |
|---|---|
| 只处理 business_critical canonical | ✅ 47/64 (17 internal excluded) |
| 不处理普通照片 | ✅ |
| 不处理 internal/_status | ✅ |
| 不删除原文件 | ✅ 全部原文件存在 |
| 不清理旧目录 | ✅ |
| 不调用 OCR/VLM | ✅ |
| 不写 95306_collection.sqlite3 | ✅ 文件不存在 |
| 不外部上传/微信发送 | ✅ |
| DB 更新 (project_archive_paths) | ✅ 39 → 60 |
| live_service alive | ✅ true (pid=72725) |
| cursor 未回退 | ✅ 8 sources |

## Commit

TBD (待填入)
