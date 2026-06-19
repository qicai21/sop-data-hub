# 图片分类 — 知识与资产(权威落点)

> 2026-06-19 从 wx-ops-agent 迁入。背景:朝钢"日现场工作记录日报"被误判成"检装车通知单"
> 进检装车链炸掉([issue](../issues/2026-06-19-朝钢检装车链-多船混合单+车号OCR失败.md))。
> 根因之一是**严格 title 锚定的分类知识只活在 wx-ops-agent 的批处理脚本里,从没接进 live**。
> 本目录把这些资产搬到主程序 sop-data-hub,作为**单一权威**,防止再次漂移。

## live 分类是怎么跑的(真实路径)

```
wx-ops-agent: thumbnail_sentinel.py  →  4 类粗筛(装卸现场照片/手写报告/检车单/其他)
              ↑ 只决定"要不要全图 OCR",缩略图读不清标题,故按版式粗判
                         │  检车单/其他 → 需全图
                         ▼
sop-data-hub: src/sop_hub/classifier/classifier.py  ← ★ live 精分类器(VLM,本类知识在此)
              · DEFAULT_CATEGORY_CARDS   每类的识别卡片(版式/标题/列特征)
              · classify() 后处理         ← 确定性闸在这(见下)
              · prompts.py build_classify_prompt   拼给 VLM 的分类 prompt
                         │  category=检装车通知单 → 
                         ▼
              runner.py / pipeline:  检装车通知单 → inspection_slip_extract 抽取链
```

**关键修复(2026-06-19,issue Fix B)**:`classifier.py` 给 `检装车通知单` 补了**标题硬锚闸**——
`detected_title` 归一(去顿号/空格)后不含"检装车通知单" → 降级 `other`,绝不进抽取链;
`prompts.py` 加了反向铁律"无该标题字样即使版式像也不判检装车单"。守门测试
`tests/test_classifier_title_gate.py`。

## 本目录资产

| 路径 | 是什么 | 状态 |
|---|---|---|
| `../../config/image_classification/business_group_image_strategies.yaml` | 按群的 类别→路由 策略(检装车通知单→inspection_slip_extract、日现场工作记录表→save_only…)。 | **参考/可后续接入** `src/sop_hub/pipeline/strategy.py`(当前路由是 strategy.py 里硬编码的,yaml 尚未 live 读取) |
| `guides/qwen3vl_inspection_slip_guide.md` 等 4 篇 | qwen3-vl 检装车单抽取/集成指南、runbook。 | 参考文档 |
| `legacy_wx_classify_scripts/classify_*.py` | wx-ops-agent 的批处理分类脚本。**含最严格的 title 锚定 prompt + 各类卡片**(`classify_manual_document_types.py` 里"检装车通知单=标题明确含'检、装车通知单'"、"日现场工作记录表=无标题/8列/默认 other")。 | **legacy 参考,非 live**。其卡片知识已 sharpen 进 live `classifier.py` 的 DEFAULT_CATEGORY_CARDS |

## 维护约定

- **改分类判定逻辑 → 改 live 的 `src/sop_hub/classifier/classifier.py` + `prompts.py`**,不是改 legacy 脚本。
- legacy 脚本只作 prompt/卡片知识的来源参考;若发现更好的判别特征,sharpen 进 live 卡片并加测试。
- strategy yaml 若要 live 化(让路由读 yaml 而非硬编码),在 `pipeline/strategy.py` 接入,然后这份 yaml 升为权威配置。
- 改 classifier/prompt 后 **kickstart live-service**(image route 在 live-service 跑)。
