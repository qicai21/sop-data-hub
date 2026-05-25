# Report: Real SOP Monitoring Plan Acceptance

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R7 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | 279854c |
| 参考预览 | reports/real_sop_monitoring_plan_preview_20260525.md |

## 1. 结论

R7 接受 R6 生成的实时 SOP 监控计划预览，作为当前 SOP-faithful baseline。

接受的不是“更聪明的推断结果”，而是当前真实 SOP 文本经 normalizer 和 compiler 之后能稳定产出的最小可读计划。

## 2. 接受原则

SOP 写了什么，系统就解析什么；SOP 没写清楚，不要脑补。

这条原则的含义是：

- SOP 已明确写出的 group token、document keyword、message keyword，保留并编译；
- SOP 只写到模糊粒度的地方，不额外补出隐藏业务语义；
- 更细的业务含义应来自 SOP 文档本身，而不是 parser/adapter 内部的暗规则。

## 3. 被接受的宽项示例

这些项在当前 SOP 文本里就是这样表达的，因此保留为宽项是正确的：

- `GROUP013 / 数据单发群 -> 文字`
- `GROUP001 / 铁晟业务工作群 -> 出港计划通知单`
- `GROUP001 / 铁晟业务工作群 -> 检装车通知单`
- `GROUP003 / 中唐特钢发运群 -> 文字放货信息`

它们看起来有宽度，但当前 SOP 原文没有给出更细说明，所以系统不应擅自缩窄。

## 4. 非目标 / 不改动项

本轮没有修改：

- `src/ops_hub/models/project_sop.py`
- `src/ops_hub/sop/monitoring_plan_compiler.py`
- `src/ops_hub/sop/monitoring_plan_preview.py`
- `tests/functional/test_real_sop_monitoring_plan_preview.py`
- `tests/functional/test_sop_normalizer.py`

本轮也没有引入：

- runtime
- publisher
- 真实微信联动
- 95306 联动
- 数据库写入
- OCR
- 额外业务推断

## 5. 当前 baseline 的意义

当前预览已经足够说明：

- 真实 4 个 SOP 可以被稳定归一化；
- 编译器能生成按群划分的 `wechat_monitoring_plan`；
- 计划是可读的，且不会把 SOP 没写清楚的部分硬补出来。

## 5. 测试

本轮为 documentation-only round，未修改代码或测试文件，因此未运行测试。

## 6. 推荐下一步

如果后续还要推进，只能走以下方向之一：

- 停止本分支，进入 review / merge 决策；
- 仅在 SOP 文档本身更明确时，再更新 normalizer 的文本映射；
- 继续保持 preview-only，不进入 runtime 或真实接入。
