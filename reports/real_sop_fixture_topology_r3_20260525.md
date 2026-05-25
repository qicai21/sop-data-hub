# Report: Real SOP markdown loader contract R3

| 字段 | 内容 |
|------|------|
| Order ID | `orders/sop_real_sop_fixture_topology_order_20260525.md` |
| 执行日期 | 2026-05-25 |
| 执行者 | local Hermes |
| 当前目录 | `/Users/qicai21/projects/repos/sop-data-hub` |
| 当前分支 | `codex/sop-real-sop-topology-audit-20260525` |
| 当前 commit | `6db84e7` (本轮提交后) |
| Python 版本 | `Python 3.11.15` |
| pip install | 未重新执行；沿用现有虚拟环境，测试可运行 |

## 1. 结论

本轮只建立了最小 markdown loader contract：真实 SOP fixture 仍以 markdown 文档形式存在，loader 只返回文件路径、首个 H1 标题和原始内容，不解析完整 SOP 业务语义。

## 2. 阅读清单

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `docs/architecture/sop_data_hub_runtime_rearchitecture_20260525.md`
- `reports/sop_data_hub_runtime_plan_branch_20260525.md`
- `tests/functional/test_sop_monitoring_plan_compiler.py`
- `tests/functional/test_sop_data_hub_source_supervision.py`
- `src/ops_hub/sop/monitoring_plan_compiler.py`
- `src/ops_hub/models/project_sop.py`
- `tests/functional/test_real_sop_fixture_contract.py`

## 3. 本轮改动

### 3.1 订单推进

- 将 order 顶部 `Current round` 从 `R2` 推进到 `R3`。
- 将 R2 段落标记为上一轮任务。
- 新增 R3：最小 markdown loader contract。

### 3.2 代码/测试

- `src/ops_hub/models/project_sop.py`
  - 新增 `MarkdownSOPFixture`。
  - 新增 `load_markdown_sop_fixture()`，只读取原始 markdown、提取首个 `# ` 标题，不解析 SOP 业务结构。
- `tests/functional/test_real_sop_fixture_contract.py`
  - 改为验证 4 份真实 SOP fixture 的 markdown loader contract。
  - 只断言文件扩展名、路径稳定、内容非空、首个 H1 标题可抽取。

## 4. 测试命令

- `pytest tests/functional/test_real_sop_fixture_contract.py -v`
- `pytest tests/functional -v`

## 5. 测试结果

- `tests/functional/test_real_sop_fixture_contract.py` -> `1 passed`
- `tests/functional -v` -> `8 passed, 1 skipped`

## 6. 失败 / skip 原因

- 本轮无失败。
- `tests/functional/test_sop_data_hub_source_supervision.py` 继续保持 `skipped`，原因是运行时 source supervision adapter contract 仍未设计。

## 7. 变更文件

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/models/project_sop.py`
- `tests/functional/test_real_sop_fixture_contract.py`

## 8. 下一步建议

- 先审阅本轮 R3 的最小 loader contract 是否足够。
- 若继续，只能在新的明确 order 下再决定是否扩展到更完整的 SOP 解析；当前不应进入 compiler/runtime/publisher。
