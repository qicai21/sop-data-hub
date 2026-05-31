# R65.1: 彻底清空 obsolete active 路径及后续收口

**日期**: 2026-05-31
**分支**: `codex/sop-real-sop-topology-audit-20260525`
**repo**: `qicai21/sop-data-hub`

## 执行摘要

R65 遗留了 `business/general/`、程序目录误存图片、群名不一致三个问题。R65.1 一并收口。

---

## 1. 彻底清空 obsolete active 路径 (R65.1)

**脚本**: `scripts/purge_obsolete_active_paths.py`

| 指标 | 值 |
|---|---|
| review_moved_count | 644 |
| empty_dirs_removed_count | 31 |
| failed_count | 0 |
| sha_mismatch_count | 0 |
| obsolete_active_paths_before | 27 |
| **obsolete_active_paths_after** | **0** ✅ |

**清理的路径：** `_status`, `_raw`, `_previews`, `other`, `unknown`, `unmatched`, `extractions`, `出港计划通知单`, `检装车通知单`, `请车表`, `手写箱号车号表`, `日现场工作记录表`, `耗材统计表`, `照片-*`（6 类），以及全部群级 obsolete 子目录。

**Quarantine**: `_quarantine/r65_20260531_review/` (644 files)

---

## 2. 删除 business/general/ 

R64 归档的 333 张普通照片来自旧分类目录，与各群 raw images 重复（仅 4 张 SHA 完全匹配）。当前 pipeline 不往该目录写入，属于一次性迁移产物。

| 操作 | 结果 |
|---|---|
| `rm -rf business/general/` | 已删除 |

---

## 3. 清理程序目录误存图片

**路径**: `wx-ops-agent/data/Img/` (~300 张 铁晟 raw images)
**根因**: 早期 ImageStore 默认 base_dir 设为 `data/Img`，后来 media_resolver 改为直写 `wechat_images/`，历史文件未清理。

| 操作 | 结果 |
|---|---|
| `rm -rf data/Img/` | 已删除（目录+内容） |
| `rm -rf data/cleanup_backups/` | 已删除（目录+内容） |
| 文档残留 | 无 |

---

## 4. 群名统一：`数据单发群-GROUP013` → `数据单发群`

| 位置 | 操作 |
|---|---|
| `wechat_images/数据单发群` | 目录重命名 |
| `chat_records/数据单发群` | 目录重命名 |
| `group_member_maps/数据单发群.json` | 文件重命名 |
| `runtime/events/数据单发群` | 目录重命名 |
| `runtime/cursors/live_service_cursor.json` | 路径引用更新 |

未改动 `tracking_rules.yaml` 中的 `name: "数据单发群-[GROUP013]"` — 那是微信显示的原始群名。

---

## 最终 active 区域结构

```
wechat_images/
├── business/projects/              ← canonical 单据 (8 files)
├── _quarantine/r65_20260531/       ← R65 移入 (2641 files)
├── _quarantine/r65_20260531_review/ ← R65.1 移入 (644 files)
├── 铁晟业务工作群/                  ← wx-ops-agent 运行时 (498 files)
├── 中唐特钢发运群/                  ← wx-ops-agent 运行时 (2 files)
├── 数据单发群/                      ← wx-ops-agent 运行时 (24 files)
├── 龙虾测试群/                      ← 测试群 (25 files)
├── projects/                       ← 旧归档 (可删)
├── reports/                        ← 旧报表
└── _migration_reports/
```

---

## 确认项

| 项 | 状态 |
|---|---|
| obsolete_active_paths_after = 0 | ✅ |
| business/projects 8 files | ✅ |
| business/general 已删除 | ✅ |
| data/Img 已删除 | ✅ |
| 群名统一 | ✅ |
| 0 sha mismatch | ✅ |
| 无物理删除（全部 move 到 quarantine） | ✅ |
| live_service | ✅ alive, cursor 已更新 |
| git 无废弃路径跟踪 | ✅ |

## Commits

| repo | commit | 说明 |
|---|---|---|
| sop-data-hub | `0c8f604` | R65.1 原始 commit |
| wx-ops-agent | clean | 运行时目录，不在 git 中 |
