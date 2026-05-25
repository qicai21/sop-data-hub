# Report: Raw Asset Bundle Registration R9

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R9 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | 7c433d7 |
| Python 版本 | Python 3.11.15 |

## 1. 结论

R9 增量更新了 order 到当前轮，并实现了本地 `RawAssetBundle` registration protocol。`MessageEvent` 通过 `raw_asset_bundle` 绑定原始资产，解决了原始图片路径、OCR JSON 路径、message metadata 路径的登记位置和绑定方式。

## 2. Order 更新

本轮先将 order 从 R8 增量推进到 R9，并把 R9 任务写成显式的 raw asset bundle registration protocol：

- `Current round` 更新为 `R9`；
- R9 明确只做本地 functional test；
- R9 明确禁止 runtime、wx-ops-agent、数据库、95306、OCR 执行、报告发送、asset 移动/复制。

## 3. 实现内容

新增的数据结构与绑定协议：

- `MessageEvent`
  - `message_id`
  - `group_id`
  - `source_agent`
  - `received_at`
  - `message_type`
  - `text`
  - `raw_asset_bundle`
- `RawAssetBundle`
  - `message_id`
  - `group_id`
  - `source_agent`
  - `received_at`
  - `raw_image_path`
  - `ocr_json_path`
  - `message_metadata_path`
  - `text`
  - `extraction_kind`
  - `registration_status`
  - `warnings`

新增协议行为：

- `register_raw_asset_bundle(...)`：生成本地资产登记结果；
- `bind_raw_asset_bundle(event, bundle)`：把 bundle 绑定到 MessageEvent；
- 缺失路径时返回 `incomplete` 和清晰 warnings，不抛异常；
- `monitoring_plan_matcher` 兼容从 `raw_asset_bundle.text` 读取文本，保持现有 matcher 行为不退化。

## 4. 测试场景

### 场景 A：image message + raw image + OCR JSON + metadata

结果：

- bundle 持有三条路径；
- event 已绑定 bundle；
- `registration_status = complete`；
- 无 crash。

### 场景 B：text message + metadata only

结果：

- bundle 只登记 metadata 路径；
- event 已绑定 bundle；
- text 保留；
- `registration_status = complete`。

### 场景 C：missing asset path

结果：

- 不 crash；
- 返回 warning；
- `registration_status = incomplete`。

## 5. 测试结果

执行命令：

```bash
pytest tests/functional -v
```

结果：

- `19 passed, 1 skipped`

skip 仍然来自 source supervision 的预期跳过测试。

## 6. 明确未做

本轮没有增加：

- runtime
- wx-ops-agent 接入
- 数据库写入
- 95306 接入
- OCR 执行
- 报告发送
- asset 移动/复制
- 生产流程修改
- 九三驱动设计扩展

## 7. 改动文件

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/sop/monitoring_plan_matcher.py`
- `src/ops_hub/sop/raw_asset_bundle.py`
- `tests/functional/test_raw_asset_bundle_registration.py`
- `reports/raw_asset_bundle_r9_20260525.md`
- `reports/github_audit_raw_asset_bundle_r9_20260525.md`

## 8. Git

- 提交：`32b1686 feat: add raw asset bundle registration protocol`
- 推送目标：`origin/codex/sop-real-sop-topology-audit-20260525`
- 提交前工作区包含 order / code / test / report / audit 的本轮变更
