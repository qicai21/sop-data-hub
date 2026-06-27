# 工单:环球信任批次号错排 + pending_freight 状态不对(中唐特钢)

- **提出日期**:2026-06-27
- **类型**:数据(lot号/状态)+ 三个系统性根因
- **状态**:✅ 数据已修(lot08);C/B 代码已修;A 确认无需改;wx_2020 no-task 待查
- **场景**:用户发现 环球信任最新批次系统排成 lot06(应 lot08),且有计划号/合同号却仍 pending_freight。

---

## 一、现象 vs 真相

**现象**:① 环球信任最新批次 = lot06,但按"第N次下达计划"应是 lot08;② 该批次有 plan_id(90260400072)+contract(HT-ZT-20260424),状态却 pending_freight(=缺货运信息)。

**真相**(逐条核实):
- 真通知单 **wx_2026-06_2020**(锦州港出港计划通知单,中唐)**捕捉到了、分类对、抽取完全正确**:八次下达计划全抽对,**第6/7次标了"公路 中唐"、第8次=6月27日 9282吨铁路汐子清底**,连"VL把6-27误读5-27"都自动纠正了(`_vl_correction`)。
- **但 `_agent_ingested: 0`**——通知单抽对了却**没入库**(`workflow_task_db` 里查无 create_release_batch 任务,node 设了任务没生成)。所以 lot08 没建出来。
- 系统里的 lot06 是**另一次 backfill** 的产物(`split_zhongtang_lots_by_plan.py` + 沈阳颐昇2.27.xlsx),它**绕过了第N次解析器**(顺序排成 lot06),且 backfill 把 **lot05 的 plan/contract 顺手分给了它**(不是任何货运消息——inbox 里压根没有带 90260400072 的货运消息,enrich 也 0 次命中环球信任)。
- 所以 **pending_freight 其实是对的**(lot08 确实没货运信息),错的是 lot 号 + 凭空的 plan/contract。

## 二、即时修复(已做)
1. 清掉 lot06 凭空的 plan_id/contract(backfill 脏数据)。
2. lot06 → **lot08**(它数据正是第八次的 9282吨/6-27/汐子)。环球信任现:lot01-05 confirmed_received、(lot06/07 公路跳号)、**lot08 pending_freight 9282吨**。

## 三、根因 + 系统性分析(三个)

| # | 根因 | 状态 |
|---|---|---|
| **A** | batch_sequence 没按"第N次下达计划" | **本就对,无需改**:解析器 `agent.py:2076 第八次→lot08` + `_filter_remarks_by_project_scope` 公路过滤都在。lot06 是 backfill 绕过解析器造的,不是解析器 bug |
| **B** | 授权通知单建出 0 批次时静默(VL误读日期当历史过滤/scope全过滤/漏建) | ✅ commit a7b381a:`_execute_create_release_batch` ingest 0 records → 数据单发群⚠️告警(船名/日期/msg_id) |
| **C** | `auto_enrich` 填齐计划号/合同号后**不推 lifecycle** → 有货运信息状态仍卡 pending_freight | ✅ commit a7b381a:中唐 enrich 成功后对 matched 批次 `advance_lifecycle(pending_freight→enriched)`(幂等+校验不倒退) |

## 四、待办
1. **wx_2020 为何没生成 create_release_batch 任务**(node 设了任务空)?待查——可能是本 session 我"手工建 lot06"时绕过了正常任务链,也可能是 image→task 路由真漏。B 的告警只 catch"任务跑了但0入库",catch 不到"任务没生成"。
2. backfill `split_zhongtang_lots_by_plan` 不该把一个 plan/contract 分给多批次、不该顺序排号(应走第N次)。

## 五、关联
- [[release_batch_business_rules]]、[[project_lifecycle_modes]](pending_freight 含义)、[[project-scope-filter-rule]](公路跳号)
- 同日 `2026-06-27-工单-中唐货运消息匹配挂起不重试.md`(出港0批次告警是其待办2)
- 代码:`workflow_task_executor`(_execute_create_release_batch 告警 / _execute_freight_detail_enrichment 推lifecycle)、`data_agent/agent.py`(第N次解析+scope过滤)
