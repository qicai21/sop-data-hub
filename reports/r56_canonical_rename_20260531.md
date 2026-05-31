# R56: 路径和仓库名收口

| 字段 | 内容 |
|------|------|
| Round | R56 |
| 执行日期 | 2026-05-31 |
| 仓库 | qicai21/sop-data-hub |

## 1. 结论

`sop-data-hub` 全仓库路径和 remote 已统一为 canonical 名称。旧 `ops-data-hub` 目录已删除。代码中无残留硬编码路径。GitHub push 受阻于网络，commit 已在本地。

## 2. 变更明细

### 2.1 Git remote
- `qicai21/ops-data-hub.git` → `qicai21/sop-data-hub.git`

### 2.2 代码硬编码路径 (7 处)
- `src/ops_hub/sop/report_intent.py`: 3 个 template_path
- `scripts/gen_jljg_excel.py`: OUT_DIR
- `src/ops_hub/__init__.py`: docstring
- `src/ops_hub/cli.py`: docstring
- `src/ops_hub/runner.py`: docstring
- `src/ops_hub/sop/source_watcher.py`: comment
- `src/ops_hub/config.py`: comment
- `scripts/business_query.py`: help text
- `scripts/jiusan_board_generate.py`: HTML 内 contract 路径

### 2.3 config / dashboard 数据 (4 处)
- `config/project_sops/jiusan.yaml`: doc_path
- `config/settings.yaml`: header comment
- `dashboard/jiusan_dashboard_data.json`: contract_path
- `dashboard/jiusan_dashboard_data.example.json`: contract_path

### 2.4 测试文件 (2 处)
- `tests/functional/test_live_service_bootstrap.py`: comments
- `tests/functional/test_report_intent_resolver.py`: 期望路径

### 2.5 旧目录
- 删除 `~/projects/repos/ops-data-hub/` (空目录，仅含空 dashboard/)

### 2.6 Hermes skills
- 37 个 active skill 文件中 `repos/ops-data-hub` → `repos/sop-data-hub` 及 `qicai21/ops-data-hub` → `qicai21/sop-data-hub`
- `.archive/` 下文件未修改

### 2.7 Memory
- memory 条目更新为 canonical 路径和 GitHub 仓库名

## 3. 残留引用（有意保留）

- `tests/functional/test_db_migration_cleanup_r21.py`: 测试检查旧 zombie DB 路径是否存在，保留 `ops-data-hub` 引用以验证已清理

## 4. Git

| 字段 | 内容 |
|------|------|
| branch | codex/sop-real-sop-topology-audit-20260525 |
| commit | 80c0b67 |
| push | 网络问题，待重试 |
