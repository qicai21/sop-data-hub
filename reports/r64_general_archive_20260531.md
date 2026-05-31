# R64: 普通材料集中归档

**日期**: 2026-05-31
**分支**: `codex/sop-real-sop-topology-audit-20260525`
**repo**: `qicai21/sop-data-hub`

## 目标

将 R62 manifest 中 `suggested_action=move` 的 333 张普通照片（非 business_critical canonical）hardlink 到 `business/general/<YYYY-MM>/<doc_type>/`。

## 产出

| 文件 | 路径 |
|---|---|
| CSV | `runtime/general_archive_r64/r64_general_archive_result.csv` |
| JSON | `runtime/general_archive_r64/r64_general_archive_result.json` |
| Summary | `runtime/general_archive_r64/r64_general_archive_summary.json` |
| 脚本 | `scripts/archive_general_materials.py` |

## 结果

| 指标 | 值 |
|---|---|
| **target** | **333** |
| **archived** | **333** |
| **failed** | **0** |
| **hardlink** | 333 / copy: 0 |
| **db_update_count** | **193** (image_ingestion_audit.project_archive_paths 更新) |
| **DB before** | 60 records with project_archive_paths |
| **DB after** | **253** records |

## 按类型分布

| document_type | count |
|---|---|
| 照片-装卸现场情况 | 123 |
| 照片-集装箱内情况和作业 | 97 |
| 照片-敞车内部情况和作业 | 57 |
| 照片-火车涂写mark | 40 |
| unknown | 13 |
| 照片-检查工人 | 3 |

## 抽样核验 (5 random)

| doc_type | inode | src | tgt |
|---|---|---|---|
| 照片-装卸现场情况 | ✓ | ✓ | ✓ |
| 照片-火车涂写mark | ✓ | ✓ | ✓ |
| 照片-集装箱内情况和作业 | ✓ | ✓ | ✓ |
| 照片-集装箱内情况和作业 | ✓ | ✓ | ✓ |
| 照片-集装箱内情况和作业 | ✓ | ✓ | ✓ |

## 确认项

| 项 | 状态 |
|---|---|
| 只处理 non-business-critical move | ✅ 333 images |
| 不处理 R63 canonical | ✅ 通过 R63 source set 排除 |
| 不删除原文件 | ✅ 0 missing sources |
| hardlink 优先 | ✅ 全部 hardlink |
| 不调用 OCR/VLM | ✅ |
| 不写 95306_collection | ✅ |
| 不外部上传/微信发送 | ✅ |
| live_service | ✅ alive, 8 sources |
| cursor | ✅ 未回退 |
| target files exist | ✅ 0 missing |

## Commit

TBD (待填入)
