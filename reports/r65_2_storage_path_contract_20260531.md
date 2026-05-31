# R65.2: 保存路径配置收口，防止旧分类目录复活

**日期**: 2026-05-31
**分支**: `codex/sop-real-sop-topology-audit-20260525`
**repo**: `qicai21/sop-data-hub`

## 目标

R65/R65.1 清理后，旧路径可能被 runner.py 新消息重新创建。本轮修正配置和代码，彻底封死旧路径写入。

## 修改的文件

| 文件 | 修改内容 |
|---|---|
| `config/storage_policy.yaml` | v1→v2：新增 `forbidden_new_writes`，禁用 `general_archive`，增加 `runtime_outputs` |
| `src/ops_hub/runner.py` | 4 处路径写入点全部改为 runtime 或 business/projects |
| `scripts/verify_storage_paths.py` | 新增：扫描 + 自测，检查 forbidden 路径 |

## storage_policy.yaml v2 新增

| 策略项 | 值 |
|---|---|
| `business_archive.general_archive_enabled` | **false** |
| `runtime_outputs.extractions_path_pattern` | `runtime/extractions/{group}/{yyyy_mm}/{doc_type}/` |
| `runtime_outputs.image_status_path_pattern` | `runtime/image_status/{group}/{yyyy_mm}/` |
| `legacy_directories.forbidden_new_writes` | 11 项：`{group}/{category}`, `{group}/extractions`, `_status`, `_raw`, `_previews`, `other`, `unknown`, `unmatched`, `business/general`, `extractions`, `{category}` |

## runner.py 旧路径写入点修改

| 位置 | 旧行为 | 新行为 |
|---|---|---|
| Step 2 (line 413) | `copy2` 到 `{group}/{category}/` | 不做 copy2，`saved_path = raw_img_path` |
| Step 3 (line 436) | JSON 写到 `{group}/extractions/{category}/` | 写到 `runtime/extractions/{group}/{yyyy_mm}/{category}/` |
| `_write_status_file` (line 52) | 写到 `{group}/_status/` | 写到 `runtime/image_status/{group}/{yyyy_mm}/` |
| `_move_processed_artifacts` (line 285) | 授权→`projects/…`，未授权→`unmatched/…` | 授权→`business/projects/…`，未授权→`runtime/unmatched/…` |

## verify_storage_paths.py 检查结果

| 检查项 | 结果 |
|---|---|
| **forbidden root violations** | **0** ✅ |
| **forbidden subdir violations** | **0** ✅ |
| `_status/` | OK (不存在) |
| `_raw/` | OK (不存在) |
| `other/` | OK (不存在) |
| `unknown/` | OK (不存在) |
| `unmatched/` | OK (不存在) |
| `extractions/` | OK (不存在) |
| `business/general/` | OK (不存在) |
| 旧分类目录 (`出港计划通知单/`, `检装车通知单/`, `照片-*` …) | OK (不存在) |
| 群级子目录 (`铁晟/检装车通知单/`, `铁晟/extractions/` …) | OK (不存在) |
| `business/projects/` | ✅ OK (8 files) |
| 群 raw image 目录 (`铁晟/YYYY-MM/`, `中唐/YYYY-MM/`, `数据单发群/YYYY-MM/`) | ✅ OK |

## self-test 路径验证

| 模拟场景 | 结果 |
|---|---|
| `铁晟业务工作群/检装车通知单/` 不会创建 | ✅ 当前不存在 |
| `铁晟业务工作群/extractions/` 不会创建 | ✅ 当前不存在 |
| `_status/` 不会创建 | ✅ 当前不存在 |
| `business/general/` 不会创建 | ✅ 当前不存在 |
| runtime extractions 路径 | `runtime/extractions/铁晟业务工作群/2026-05/检装车通知单/` |
| runtime image_status 路径 | `runtime/image_status/铁晟业务工作群/2026-05/` |
| canonical archive | `business/projects/` (exists) |

## 确认项

| 项 | 状态 |
|---|---|
| business/general exists? | **否** ✅ |
| old group/category exists? | **否** ✅ |
| old group/extractions exists? | **否** ✅ |
| live_service | ✅ alive, 8 sources |
| cursor | ✅ 未回退 |
| message_inbox | ✅ 可查询 |
| 不删除/移动文件 | ✅ |
| 不写 95306_collection | ✅ |
| 不上传/发送 | ✅ |

## Commit

TBD (待填入)
