# Report: Minimal SopNormalizer Boundary & Execution

| 字段 | 内容 |
|---|---|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R5 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | __PENDING_CODE_COMMIT__ |

## 1. 结论

本轮实现了一个最小 `SopNormalizer`：只读取真实 SOP markdown fixture，抽取 `project_id`、group token、document/message keywords、monitoring entries，并输出 normalized project_sops。

没有接 compiler，没有碰 runtime / publisher / wx-ops-agent / rail95306-sync，没有引入数据库访问，没有做 OCR，也没有做 AI 语义理解。

## 2. Normalizer 应负责什么

最小 normalizer 的职责是把原始 markdown fixture 变成可消费的结构化原料：

- 读取 markdown fixture
- 提取 `project_id`
- 提取 `project_name`
- 提取 raw group token，例如 `GROUP001`
- 提取 document keywords
- 提取 message keywords
- 提取 monitoring entries
- 输出 normalized project_sops

这属于“原始文档结构归一化”，不是业务决策。

## 3. Normalizer 不应负责什么

Normalizer 不应承担：

- AI 理解
- OCR
- runtime scheduling
- compiler invocation
- WeChat 收发
- DB reads / writes
- publisher 行为
- 监控计划生成
- 业务语义推断

这些职责一旦进入 normalizer，边界就会开始退化成第二个 compiler。

## 4. 归一化输出形状

当前最小输出形状：

```text
NormalizedProjectSOP
- project_id
- project_name
- source_path
- source_title
- group_tokens
- document_keywords
- message_keywords
- monitoring_entries
```

`monitoring_entries` 当前承载原始来源行的结构化信息，例如：

- source_type
- channel
- group_token
- group_name
- raw keywords
- document_keywords
- message_keywords

## 5. Current implementation assessment

### `SopNormalizer`

位置：`src/ops_hub/models/project_sop.py`

当前实现做了这些事：

- 读取 `.md` fixture
- 从 metadata 表提取项目 key / 项目名称
- 用显式 alias 表归一化 project_id
- 从全文提取 `[GROUPxxx]` token
- 从“数据源与群 / 数据源与入口”表提取 monitoring entries
- 从原始 token 和显式短语提取 document/message keywords

结论：实现仍停留在最小归一化层，没有越界到 compiler/runtime/business inference。

### 兼容性

原有的 `load_markdown_sop_fixture()` 仍然保留，loader contract 没有被破坏。

## 6. Current tests assessment

### 新增 `tests/functional/test_sop_normalizer.py`

覆盖点：

- 4 个真实 SOP fixture 都能被归一化读取
- project_id 与 group token 可提取
- document/message keywords 可提取
- monitoring entries 可提取
- 原始路径与 H1 标题保留

结论：测试边界合适，没有扩展到 compiler 级别，也没有触发 runtime。

### 现有 functional tests

- `tests/functional/test_sop_monitoring_plan_compiler.py` 保持通过
- `tests/functional/test_sop_data_hub_source_supervision.py` 仍为 skipped，状态正确
- `tests/functional/test_real_sop_fixture_contract.py` 保持通过

## 7. 失败与修正记录

本轮实现中出现过两类边界问题并已修正：

- `项目 key` 中带反引号，导致 `project_id` 未清洗；已通过 `_clean_cell_text()` 归一化
- 九三 SOP 的 section 名称是 `数据源与入口`，不是 `数据源与群`；已增加 section fallback

修正后功能测试全部通过。

## 8. 测试结果

执行命令：

```bash
pytest tests/functional/test_sop_normalizer.py -v
pytest tests/functional -v
```

结果：

- `tests/functional/test_sop_normalizer.py` -> 3 passed
- `pytest tests/functional -v` -> 11 passed, 1 skipped

## 9. 改动文件

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/models/project_sop.py`
- `tests/functional/test_sop_normalizer.py`

## 10. Git

本轮完成后将按 whitelist 提交并 push。

## 11. 推荐下一步

如果继续推进，下一步只应该是一个更薄的 contract：

- 为 normalized project_sops 增加一个只做序列化/加载的轻量 contract；
- 不要直接进入 compiler 或 runtime；
- 不要让 normalizer 开始解释业务关系。
