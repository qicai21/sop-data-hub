# R60: storage_policy.yaml 全局保存契约

**日期:** 2026-05-31
**分支:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `0af5bb1`
**仓库:** `qicai21/sop-data-hub`

---

## 1. 新增配置文件路径

```
config/storage_policy.yaml
```

## 2. storage_policy.yaml 主要结构

| Section | 说明 |
|---------|------|
| `version` | 1 |
| `asset_root` | `/Users/qicai21/Documents/bussiness-artifacts/wechat_assets` |
| `raw_standard_image` | 原始标准图：owner=wx-ops-agent, immutable, 缺图→waiting_media |
| `business_archive` | 业务归档：owner=sop-data-hub, project/general 双路径, hardlink 优先 |
| `canonical_documents` | 出港计划通知单 (release) + 检装车通知单 (inspection): business_critical |
| `general_documents` | 现场照片, 其他照片, unknown: archive_to_general |
| `legacy_directories` | 禁止写入清单 + 清理候选 + quarantine_first 模式 |
| `manifest` | preferred_backend=message_inbox, 15 个 required_fields |
| `media_types` | image=process_by_policy, video=record_only, file=record_only |
| `project_override_contract` | 项目 YAML 的 artifact_policy.key 覆盖规则 |

## 3. 责任边界

| 角色 | 职责 |
|------|------|
| **wx-ops-agent** | 原始微信消息账本、媒体下载状态、不可变标准图落盘；不判断 SOP、不建业务目录、不建业务 JSON |
| **sop-data-hub** | 图片分类、OCR/VLM、SOP 命中、项目归档、JSON 保存、general 归档、message_inbox/manifest |
| **项目 SOP** | 项目专属覆盖（artifact_policy）：文档类型、媒体类型、路径模式、是否归档 |

## 4. 项目 SOP 如何覆盖

项目 YAML (`config/project_sops/<project>.yaml`) 中新增 key `artifact_policy`，可覆盖：

- `document_types` — 扩展或重新定义文档类型
- `media_types` — 覆盖 video/file 默认行为
- `path_pattern` — 自定义归档路径
- `archive_to_project` / `archive_to_general` — 归档目标
- `require_json` — 是否要求 JSON
- `allowed_extensions` — 允许的文件扩展名

示例（仅供说明，未写入项目 YAML）：
```yaml
artifact_policy:
  overrides:
    document_types:
      现场照片:
        archive_to_project: true
        document_stage: "site_photo"
    media_types:
      file:
        save_external_copy: true
        archive_to_project: true
        allowed_extensions: [".xlsx", ".pdf"]
```

## 5. 视频/file 当前默认处理

| 类型 | 默认 | 保存外部副本 |
|------|------|------------|
| image | process_by_policy | — |
| video | record_only | false |
| file | record_only | false |

项目可通过 `artifact_policy.overrides.media_types` 覆盖。

## 6. validate 输出摘要

```json
{
  "ok": true,
  "policy_path": "config/storage_policy.yaml",
  "asset_root": "/Users/qicai21/Documents/bussiness-artifacts/wechat_assets",
  "canonical_documents": ["出港计划通知单", "检装车通知单"],
  "media_types": {
    "image": "process_by_policy",
    "video": "record_only",
    "file": "record_only"
  },
  "has_project_override_contract": true
}
```

Domain checks:
- `is_canonical_document("出港计划通知单")` → True
- `is_canonical_document("检装车通知单")` → True
- `is_canonical_document("现场照片")` → False
- `is_general_document("现场照片")` → True
- `resolve_document_stage("出港计划通知单")` → "release"
- `resolve_document_stage("检装车通知单")` → "inspection"

## 7. live_service alive 状态

✅ alive=true, pid=62295, processed=0

## 8. cursor 状态

✅ cursor_source_count=8, cursor_latest_local_id=2337（未回退）

## 9. 本轮未做文件迁移/清理声明

- 未移动任何 wechat_images
- 未删除任何旧图片
- 未清理 extractions
- 未更新 image_ingestion_audit
- 未修改 wx-ops-agent
- 未修改 runner 归档行为
- 未建 message_inbox
- 未触发外部上传/微信发送

## 10. 新增代码文件

| 文件 | 说明 |
|------|------|
| `config/storage_policy.yaml` | 全局存储契约（5K） |
| `src/ops_hub/sop/storage_policy.py` | Python 读取/校验模块（~180 行） |

CLI: `python -m ops_hub.sop.storage_policy --validate`

## 11. commit sha

```
0af5bb1
```

已推送至 `origin/codex/sop-real-sop-topology-audit-20260525`。
