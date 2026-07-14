# 工单: `all_loaded` 批次不应参与新检装车匹配

## 现象

2026-07-14 中唐特钢看板出现 1 条 `pending_review` 检装车候选：

- 图片：`1204_5fbebb7fb34a4ca767fe38d064c8bf60.jpg`
- 内容：`宝丽 / 汐子 / 铁矿 / 55车`
- 当前业务事实：中唐特钢正在发运的只有 `宝丽 lot02`

但系统仍把该候选判成“多候选 / 待人工确认”。

## 根因

`all_loaded` 的业务语义已经是“本 lot 发运完毕，不应继续承接新的检装车/发运匹配”，
但代码里有两处仍把它当作开放批次：

1. `BusinessDataAgent.upsert_release_dispatch_match_rule()`
   - 旧逻辑把 `all_loaded / tracking / delivered` 都映射成
     `release_dispatch_match_rules.status='active'`
   - 导致已发完 lot 仍留在检装车匹配池

2. `infer_candidate_context._try_evidence_scoring()`
   - 旧逻辑对 `release_batches` 的证据打分范围包含
     `all_loaded / tracking / delivered`
   - 即使 rule 层收紧，推断层仍可能把候选反推回旧 lot

## 修复目标

1. 只有 `pending_freight / enriched / loading` 可以参与新检装车匹配。
2. `all_loaded` 及之后阶段只能保留审计/跟踪语义，不得再进入新发运分配。
3. 增加回归测试，防止同船多 lot 场景再次把新检装车挂到已发完 lot。

## 修复方案

1. 将 `release_dispatch_match_rules` 映射改为：
   - `pending_freight / enriched / loading -> active`
   - `all_loaded / tracking / delivered / confirmed_received / closed -> completed`
2. 将 `infer_candidate_context` 的开放批次证据打分范围收紧为：
   - `enriched / loading`
3. 补跑 `refresh_release_dispatch_match_rules()`，让当前运行库立即收口。

## 回归测试

- `tests/test_data_agent.py`
  - `test_all_loaded_release_batch_generates_completed_dispatch_match_rule`
  - `test_infer_candidate_context_ignores_all_loaded_batches`

## 期望结果

- `宝丽 lot01(all_loaded)` 不再和 `宝丽 lot02(loading)` 同时命中新检装车。
- 同船新检装车只会命中仍开放的 lot；若无开放 lot，则按既有规则挂起。
