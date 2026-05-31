# R56: 路径和仓库名收口（含验收修复）

| 字段 | 内容 |
|------|------|
| Round | R56 |
| 执行日期 | 2026-05-31 |
| 仓库 | qicai21/sop-data-hub |

## 1. 结论

`sop-data-hub` 全仓库路径和 remote 已统一为 canonical 名称。旧 `ops-data-hub` 目录已删除。
代码区零残留硬编码路径。wx-ops-agent 默认路径已修复。

## 2. 变更明细

### 2.1 Git remote
- `qicai21/ops-data-hub.git` → `qicai21/sop-data-hub.git`

### 2.2 sop-data-hub 代码硬编码路径 (10 处)
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

## 3. 验收轮修复（补充）

### 3.1 wx-ops-agent 默认路径
- `src/wechat_ops_agent/storage/paths.py:8` `_DEFAULT_OPS_DATA_HUB_ROOT` → `sop-data-hub`
- commit: `wx-ops-agent@2c1c4cd` (branch: codex/sync-to-github)

### 3.2 README.md
- 标题 `# ops-data-hub` → `# sop-data-hub`
- ASCII 架构图内名称修正
- 项目结构树 `ops-data-hub/` → `sop-data-hub/`

### 3.3 pyproject.toml
- `name = "ops-data-hub"` → `name = "sop-data-hub"`

### 3.4 launchd
- 无相关 plist 文件（项目使用 live_service 进程管理，非 launchd）

## 4. 验收结果

| 检查项 | 结果 |
|--------|------|
| `grep -R "ops-data-hub" src/ scripts/` (excl. tests) | **零命中** |
| `grep -R "ops-data-hub" config/` | **零命中** |
| wx-ops-agent 默认路径 | **已修正** |
| 旧 ops-data-hub 目录 | **已删除** |
| live_service 默认路径 | **指向 sop-data-hub**（通过 `get_ops_data_hub_src()` 解析） |

## 5. 残留引用（有意保留）

- `tests/functional/test_db_migration_cleanup_r21.py`: zombie DB 清理测试
- `reports/`: 历史审计文档（不修改）
- wx-ops-agent 中函数/模块名 `ops_data_hub`：代码标识符，非路径

## 6. Git

| 字段 | 内容 |
|------|------|
| branch | codex/sop-real-sop-topology-audit-20260525 |
| commits | 80c0b67, 63f18b7, 36fc6f5 |
| push | ✅ |
