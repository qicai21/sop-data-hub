# R65: 清理旧分类副本和旧 extractions

**日期**: 2026-05-31
**分支**: `codex/sop-real-sop-topology-audit-20260525`
**repo**: `qicai21/sop-data-hub`

## 目标

将已归档或应作废的旧路径从 active 区域移动到 quarantine，不物理删除。

## 产出

| 文件 | 路径 |
|---|---|
| CSV | `runtime/storage_cleanup_r65/r65_cleanup_result.csv` |
| JSON | `runtime/storage_cleanup_r65/r65_cleanup_result.json` |
| Summary | `runtime/storage_cleanup_r65/r65_cleanup_summary.json` |
| Active check | `runtime/storage_cleanup_r65/r65_active_path_check.txt` |
| Quarantine list | `runtime/storage_cleanup_r65/r65_quarantine_paths.txt` |
| Obsolete list | `runtime/storage_cleanup_r65/r65_obsolete_paths.txt` |
| 脚本 | `scripts/cleanup_legacy_storage.py` |

## 结果

| 指标 | 值 |
|---|---|
| **quarantine_root** | `_quarantine/r65_20260531/` |
| **moved_count** | **2641** |
| **failed_count** | **0** |
| **skipped_review_count** | ~40 (保留在原位) |
| **skipped_protected_count** | 344 (business/ 区域) |
| **empty_dirs_removed_count** | **27** |
| **sha_mismatch** | **0** |
| **source_exists_after** | **0** (全部成功移动) |
| **active_obsolete_path_remaining_count** | ~40 (均为 review 条目) |

## 清理明细

| source_area | moved |
|---|---|
| internal (_status/_raw/_previews) | 1115 |
| legacy (other/unknown) | 1082 |
| raw (old classified copies) | 339 |
| group_extractions | 89 |
| unmatched (trash) | 16 |

## 保留的有效路径

| 路径 | 状态 | 文件数 |
|---|---|---|
| `business/projects/` | ✅ intact | 8 |
| `business/general/` | ✅ intact | 333 |
| `_quarantine/r65_20260531/` | ✅ created | 2641 |

## 已作废并清理的路径

- `_status/` — CLEAN
- `_raw/` — CLEAN
- `_previews/` — CLEAN
- `other/` — CLEAN
- `extractions/` — EMPTY
- `照片-装卸现场情况/` — CLEAN
- `照片-集装箱内情况和作业/` — CLEAN
- `照片-敞车内部情况和作业/` — CLEAN
- `照片-火车涂写mark/` — CLEAN
- `照片-检查工人/` — CLEAN

## 保留（review，不动）

- `unknown/` — 2 items
- `unmatched/` — 1 item
- `出港计划通知单/` — 2 items
- `检装车通知单/` — 22 items
- `请车表/` — 1 item
- `手写箱号车号表/` — 3 items
- `日现场工作记录表/` — 7 items
- `耗材统计表/` — 2 items

以上均为 R62 manifest 中 `suggested_action=review` 的条目，按规范保留。

## 随机校验 (5)

| source | quarantine | SHA | src_gone |
|---|---|---|---|
| 铁晟/extractions/检装车/1153_...jpg | ✓ | ✓ | ✓ |
| unknown/unknown/1457_...jpg | ✓ | ✓ | ✓ |
| unmatched/.../51_ba4d...jpg | ✓ | ✓ | ✓ |
| unknown/.../migrated_787_...jpg | ✓ | ✓ | ✓ |
| unknown/.../204_1d37...jpg | ✓ | ✓ | ✓ |

## 确认项

| 项 | 状态 |
|---|---|
| 无物理删除（move 到 quarantine） | ✅ |
| business/projects 不受影响 | ✅ 8 files |
| business/general 不受影响 | ✅ 333 files |
| sop_agent.db 未更新路径 | ✅ |
| 不写 95306_collection | ✅ |
| 不调用 OCR/VLM | ✅ |
| 不外部上传/微信发送 | ✅ |
| live_service | ✅ alive, 8 sources |
| cursor | ✅ 未回退 |

## Commit

TBD (待填入)
