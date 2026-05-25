# Report: Real SOP Loader Boundary Audit

| 字段 | 内容 |
|---|---|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R4 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前 commit | 320d97f |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |

## 1. Audit 结论

本轮只做 loader schema boundary audit，不接 compiler，不扩展 runtime / publisher / wx-ops-agent / rail95306-sync，不改业务代码。

当前 loader 边界是可接受的：`load_markdown_sop_fixture()` 仅做文件读取、首个 H1 标题提取、raw content 保留，没有引入业务语义解析。

## 2. Loader 应负责什么

Loader 的职责应限制在 raw markdown ingest：

- file path
- raw content
- first markdown title
- basic markdown heading list
- raw group tokens such as `[GROUP001]`
- raw data-source blocks if directly extractable without inference

这类输出属于“文档事实层”，不包含业务判断。

## 3. Loader 明确不应负责什么

Loader 不应承担：

- business semantic inference
- SOP node normalization
- monitoring requirement generation
- project merging
- WeChat monitoring plan compilation
- compiler invocation
- database reads/writes
- runtime scheduling

这些职责会把 loader 变成第二个 compiler，破坏边界。

## 4. 近期待定的 raw markdown loader schema

建议的 raw loader schema 保持最小且稳定：

```text
MarkdownSOPFixture
- file_path: Path
- title: str
- content: str
```

如果未来需要进一步暴露文档结构，优先增加“原始结构字段”，而不是直接映射业务语义字段。

## 5. Future normalizer 应负责什么

未来若引入 `SopNormalizer` 或等价层，其职责应是：

- turn raw SOP markdown structure into normalized project_sops
- map headings/tables to sop_nodes
- extract explicit monitoring requirements if present
- flag missing structured fields
- produce data suitable for compiler input

这一步才可以开始从 raw 文档走向规范化业务输入。

## 6. Compiler 应负责什么

`SopMonitoringPlanCompiler` 应保持为唯一的监控计划编译层，负责：

- merge normalized project_sops by channel/group
- remove duplicated watch items
- preserve candidate_projects
- generate target_sop_nodes mapping
- return channel monitoring plan

Loader 不应直接做这些事。

## 7. Current implementation assessment

### `load_markdown_sop_fixture()`

位置：`src/ops_hub/models/project_sop.py`

当前实现：

- 读取 markdown 文件原文
- 扫描首个 `# ` 行作为 title
- 原样返回 `file_path` / `title` / `content`

结论：当前实现严格停留在 loader scope 内，没有跨到业务语义层，也没有接 compiler。

### `SopMonitoringPlanCompiler`

位置：`src/ops_hub/sop/monitoring_plan_compiler.py`

当前实现已经是独立的 channel/group 计划编译器，负责：

- 过滤非 wechat channel
- 按 group_id 聚合 watch_items
- 合并 candidate_projects
- 生成 target_sop_nodes
- 去重 text patterns

结论：compiler 职责清晰，应该继续留在下游，不应被 loader 反向吸收。

## 8. Current tests assessment

### `tests/functional/test_real_sop_fixture_contract.py`

结论：范围合适，没有 overreach。

它只验证：

- fixture 是 markdown 文件
- loader 返回原始 file_path
- loader 提取首个 H1 title
- loader 保留 raw content

它没有解析 SOP 业务节点，没有生成 monitoring plan，没有触发跨模块调用，保持了正确的 loader contract。

### `tests/functional/test_sop_monitoring_plan_compiler.py`

结论：这是 compiler contract，不属于 loader boundary。

它验证 compiler 的分组、去重、candidate_projects、target_sop_nodes 行为，保持独立是正确的。

### `tests/functional/test_sop_data_hub_source_supervision.py`

结论：当前仍是 skip，原因合理。

它明确预留了未来 runtime source supervision 约定，但目前没有 adapter 设计，因此保持 skipped 是正确状态，不应在本轮解除。

## 9. Safest next step

最安全的下一步不是继续扩 loader，也不是接 compiler。

推荐下一步 order：

- 在当前 loader / normalizer 边界下，补一层显式 `SopNormalizer` 设计审计，定义 raw markdown 到 normalized project_sops 的输入输出契约；
- 只在 order 明确要求时再实现该层。

如果后续要继续实现，优先是“边界设计”，不是“功能扩张”。

## 10. 改动文件

- `reports/real_sop_loader_boundary_audit_20260525.md` 新增
- `reports/github_audit_real_sop_loader_boundary_20260525.md` 新增

## 11. Git

本轮不改业务代码，不跑测试，不改 order。
