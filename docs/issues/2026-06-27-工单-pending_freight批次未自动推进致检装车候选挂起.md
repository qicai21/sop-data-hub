# 工单:批次有货运信息(plan_id+合同)但卡在 pending_freight 未自动推进 → 检装车候选 no_open_batch 挂起

- **类型**:代码/管线缺陷
- **发现日期**:2026-06-27
- **发现会话**:数据运维(只改数据/批次,不改代码)
- **处理归属**:系统开发会话
- **严重度**:中 —— 检装车候选挂起、需人工手动推进批次状态才能匹配入库

## 现象

中唐特钢 丰收散运 lot08(`c49a7b5fdc78ce70727e43c16774f9ab1b2fa236`)的检装车候选挂起,reason=`no_open_batch`。

排查发现:
- 该批次**已有货运信息**:`plan_id=90260600088`、`contract_no=ZLZT-2026062401`(用户已补,看板可见)。
- 但 `dispatch_status` 仍为 **`pending_freight`**,从未推进到 `loading`/`enriched`。
- 检装车匹配器 `match_release_batch.py` 的 `_OPEN_STATUSES = ("loading", "enriched")`(line 45),**不含 pending_freight** → 查无 open 批次 → `no_open_batch`(line 103)。

## 关键证据(排除"时间先后")

- 批次 created 2026-06-26 03:36,updated 2026-06-27 07:01;候选 created 2026-06-27 13:35 —— **货运信息在前,候选在后**,不是"挂起早于货运补充"的时间问题。
- **order_identifier 不是门槛**:中唐 44 个 confirmed_received(已走完 loading→完毕)批次**全部 order_identifier 为空**,却都正常推进过。丰收 lot08 同样有 plan_id+contract、同样无 order_identifier,本应能像那 44 个一样推进到 loading,**却没推进**。
- 对照:贝拉 lot05 同期同类,`dispatch_status=loading`(可匹配);丰收 lot08 / 环球信任 lot06 都卡 `pending_freight`(plan_id+contract 都在)。

## 根因(待开发确认)

`pending_freight → loading/enriched` 的自动推进,对"货运信息经非 freight_detail 文本途径补齐"(如出港计划通知单直接带出 plan_id/contract,或人工补)的批次**没有触发**。即:批次拿到 plan_id+contract 后,缺少一个"货运信息齐 → 推进到 loading"的兜底转换,使其一直停在 pending_freight,被检装车匹配器排除。

建议排查:
- `src/sop_hub/sop/enrich_release_batch.py`(enrich 后是否/何时 set dispatch_status=enriched/loading)
- `src/sop_hub/sop/lifecycle*.py`(pending_freight → enriched/loading 的转换触发条件)
- 是否应:批次 plan_id+contract_no 齐即自动 enriched;或匹配器 `_OPEN_STATUSES` 纳入 pending_freight(但需评估副作用);或补一个周期性"freight 齐则推进"的兜底。

## 期望

货运信息(至少 plan_id+contract)补齐的批次应自动进入 loading/enriched,使检装车候选能正常匹配,无需人工手动改状态。同类隐患:环球信任 lot06(`*`,pending_freight,plan_id+contract 已在)。

## 数据侧已临时处置(本会话)

- 手动 `update_release_dispatch_status(丰收 lot08, 'loading')` 解除挂起,使候选可匹配。
- 环球信任 lot06 暂未动(无挂起候选触发,留待开发修根因)。
