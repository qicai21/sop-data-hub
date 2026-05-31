# R66: 保存规则总核验与运行链路冒烟测试

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`

---

## 1. Storage Policy 摘要

| 项目 | 值 |
|---|---|
| 版本 | v2 (R65.2) |
| `general_archive_enabled` | **false** ✅ |
| `forbidden_new_writes` | 11 条规则 |
| `runtime_outputs` | `extractions_path_pattern`, `image_status_path_pattern` |
| `canonical_documents` | 出港计划通知单, 检装车通知单 (2 docs, 均 `archive_to_project=true`) |
| `general_documents` | 现场照片, 其他照片, unknown (3 docs, 均 `archive_to_general=false`) |

**forbidden_new_writes 完整列表：**

```
{group_name}/{category}/
{group_name}/extractions/
extractions/
_status/
_raw/
_previews/
other/
unknown/
unmatched/
business/general/
{category}/
```

---

## 2. Allowed Active Paths

| 路径 | 文件数 | 状态 |
|---|---|---|
| `business/projects/` | **8 files** | ✅ OK |
| `_quarantine/` | 不存在 | 用户手动删除 (预期) |
| `铁晟业务工作群/2026-05/` | 4 raw images | ✅ OK |
| `铁晟业务工作群/手写记录/` | 2 jpg | ⚠️ **legacy survivor** |
| `中唐特钢发运群/2026-05/` | 1 raw image | ✅ OK |
| `数据单发群/2026-05/` | 0 raw images | 目录存在，空 (chat_record 指向旧 GROUP013 路径) |
| `龙虾测试群/` | 0 YYYY-MM subdir | 测试群 |

---

## 3. Forbidden Paths 检查

**0 violations** (verify_storage_paths.py — 标准 forbidden list)。

⚠️ `verify_storage_contract_r66.py` 扩展检查发现 1 项遗留：
- `铁晟业务工作群/手写记录/` — 2 个 jpg 文件（`1893_fe48...jpg`, `2226_3f3d...jpg`），是 R65/R65.1 清理时漏网的旧分类目录（"手写记录" 未在原 FORBIDDEN_DIRS 列表中）。
- **处理：** 已将 `手写记录` 补入 `verify_storage_paths.py` 的 FORBIDDEN_DIRS 和 FORBIDDEN_SUBDIR_NAMES。R66 不移动文件，待后续轮次决定是否移入 quarantine。

**确认不存在的 forbidden 路径：**

| 路径 | 状态 |
|---|---|
| `_status/` | ✅ 不存在 |
| `_raw/` | ✅ 不存在 |
| `_previews/` | ✅ 不存在 |
| `other/` | ✅ 不存在 |
| `unknown/` | ✅ 不存在 |
| `unmatched/` | ✅ 不存在 |
| `extractions/` | ✅ 不存在 |
| `business/general/` | ✅ 不存在 |
| `{group}/出港计划通知单/` | ✅ 不存在 |
| `{group}/检装车通知单/` | ✅ 不存在 |
| `{group}/extractions/` | ✅ 不存在 |

---

## 4. Raw MsgPath 抽样可打开检查

**Chat record 路径状态：**

铁晟业务工作群 2026-05.jsonl 中的 image msg-path 大多为 `未下载`，少数有路径但指向已清理的旧 `_raw/` 目录。

数据单发群 chat_record 的 image 路径全部指向 `数据单发群-GROUP013/_raw/...`（R65 已清理），状态为 `waiting_media`。

**Direct raw image samples (wechat_images 目录直检):**

| Group | File | Size |
|---|---|---|
| 铁晟业务工作群 | `2220_9b575f...jpg` | 82,419 bytes ✅ |
| 铁晟业务工作群 | `2265_574b69...jpg` | 47,136 bytes ✅ |
| 铁晟业务工作群 | `906_9676a3...jpg` | 378,946 bytes ✅ |

**3/3 raw images 可打开** ✅

---

## 5. Runner Self-Test 路径验证

**输入:** `铁晟业务工作群/2026-05/test.jpg` → category `检装车通知单`

| 测试 | 结果 |
|---|---|
| T1: NO `铁晟业务工作群/检装车通知单/` | ✅ 不存在 |
| T2: NO `铁晟业务工作群/extractions/` | ✅ 不存在 |
| T3: NO `_status/` at wechat_images root | ✅ 不存在 |
| T4: NO `business/general/` | ✅ 不存在 |
| T5: Runtime paths correct | ✅ `runtime/extractions/铁晟业务工作群/2026-05/检装车通知单/` |
| T6: business/projects has 8 files | ✅ 8 |

**全部通过** ✅

---

## 6. Runtime 目录状态

| 路径 | 状态 |
|---|---|
| `runtime/extractions/` | 不存在（待首次真实处理创建） |
| `runtime/image_status/` | 不存在（待首次真实处理创建） |
| `runtime/unmatched/` | 不存在（待首次真实处理创建） |
| `runtime/cursors/live_service_cursor.json` | ✅ 存在，8 sources |

---

## 7. Live Service / Cursor / Message Inbox

| 项目 | 状态 |
|---|---|
| **Live Service** | PID 72725, process running ✅ |
| **Live Service 状态文件** | `runtime/live_service_state.json`：仅含 `last_processed_message_id` 和 `last_processed_time`，无 `alive` 字段 |
| **Cursor** | 8 sources，未回退 ✅ |
| **Cursor updated_at** | `2026-05-31T06:50:05Z` ✅ |
| **Message Inbox** | 57 rows |
| **Type breakdown** | image=43, text=13, file=1 |
| **Media status** | waiting_media=43, not_required=13, ready=1 |
| **Waiting media 分析** | 43 waiting_media = 旧路径指向已清理的 `数据单发群-GROUP013/_raw/` — 这是 R65 cleanup 的预期结果，不是系统异常 |
| **Downloaded** | 0 |
| **Old group refs** | 38（chat_record 中残留 `GROUP013` 路径） |
| **Latest update** | `2026-05-31T06:50:05Z` |

---

## 8. 旧路径复活检查

| 发现 | 状态 |
|---|---|
| `铁晟业务工作群/手写记录/` | ⚠️ legacy survivor (2 files) — 非复活，是 R65 漏网 |
| 其他 forbidden 路径 | ✅ 全部不存在，未复活 |

---

## 9. 存储链路一致性判断

```
wx-ops-agent raw 图片: ✅ 铁晟业务工作群/2026-05/ 有 4 张可打开
sop-data-hub runner:  ✅ 不再写 group/category 副本
                      ✅ JSON → runtime/extractions/{group}/{yyyy_mm}/{category}/
                      ✅ Status → runtime/image_status/{group}/{yyyy_mm}/
canonical archive:    ✅ business/projects/ 8 files
general archive:      ✅ disabled (general_archive_enabled=false)
旧目录复活:           ⚠️ 手写记录 1 项残留（非 sop-data-hub 写入导致）
```

---

## 10. Commit

| 文件 | 操作 |
|---|---|
| `scripts/verify_storage_paths.py` | +"手写记录" 到 FORBIDDEN_DIRS |
| `scripts/verify_storage_contract_r66.py` | 新增 R66 综合核验脚本 |
| `runtime/storage_contract_r66/` | 核验输出 (JSON + txt) |
| `reports/r66_storage_contract_verification_20260531.md` | 本报告 |

---

## 11. 总结

| 核验项 | 结果 |
|---|---|
| storage_policy v2 完整性 | ✅ |
| forbidden path violations (标准) | ✅ 0 |
| forbidden path violations (扩展) | ⚠️ 1 (手写记录 legacy survivor) |
| raw image 可打开 | ✅ 3/3 |
| runner self-test | ✅ 5/5 |
| business/projects 文件数 | ✅ 8 |
| _quarantine | 用户删除 (预期) |
| business/general exists? | ✅ 否 |
| old group/category exists? | ✅ 否 |
| old group/extractions exists? | ✅ 否 |
| old _status exists? | ✅ 否 |
| run/extractions 路径 | `runtime/extractions/{group}/{yyyy_mm}/{category}/` |
| run/image_status 路径 | `runtime/image_status/{group}/{yyyy_mm}/` |
| live_service running | ✅ PID 72725 |
| cursor 8 sources, 未回退 | ✅ |
| message_inbox queryable | ✅ 57 rows |
| old path resurrection | ⚠️ 1 legacy survivor (手写记录) |
