# 缺口:出港计划通知单日期容错 + 中唐货运文本提取

- **提出日期**:2026-06-23(测试覆盖审计产出)
- **类型**:健壮性 / 功能补全
- **背景**:审计"出港计划通知单建批次 + 货运信息捕获"的测试覆盖,发现 2 个真实缺口(均为代码/架构,非补测试可解)。A4(特约事项→lot1)经核实已被 `test_single_release_plan_without_explicit_sequence_defaults_to_lot01` 覆盖,非缺口。

## 缺口1:出港计划通知单"放货日期"VLM 误读 → 重复建批(A1-date)
- **现状**:`agent.py:1831` `batch_key = base|dest|batch_date_key|seq`,dedup 纯按 batch_key(`get_by_batch_key` + `ON CONFLICT(batch_key)`)。通知日期 VLM 误读(6月23→5月23)→ batch_key 变 → **建成新批(dup)**,且 A2"第N批→lotN"也被破坏(同序号不同日期=两条)。
- **为何不能简单修**:日期是**区分不同航次同序号 lot 的依据**(代码注释实证:c600 notice=5/11 lot01 与 9918a9 notice=5/29 lot01 是**合法的两个** lot01,不能合并)。若"按 ship+cargo+dest+sequence 忽略日期去重",会错误合并合法不同航次——与 2026-06-23 朝阳 rendezvous 退化同类的回归风险。
- **需先定策略(业务决策)**,候选方案:
  - (a)**安全检测**:新批与某**OPEN(loading/enriched)**批次 (project,ship,cargo,dest,sequence) 相同但日期不同 → 判为疑似日期误写,**不静默新建**,而是更新现有 / 挂 review 告警(新航次同序号 lot 只在旧批 closed 后出现,故 OPEN 重叠基本=误写)。残留风险:航次重叠(罕见)。
  - (b)纯告警:仍建批但打 WARN,人工核。
  - (c)VLM 日期与表头/上下文交叉校验(月份跳变检测)。
- **待办**:用户定策略 → 实现 + 守门测试(含 c600/9918a9 合法双 lot01 不被误合的回归保护)。

### ✅ 结案(缺口1,2026-06-23)— 采策略(a)归并+告警
- **决策**:用户选 **(a) 归并到现存 OPEN 同序号批 + 告警**。
- **实现**(`data_agent/agent.py`):
  - 新增 `_OPEN_DISPATCH_STATUSES=("loading","enriched","pending","review_needed")` + `_find_open_batch_same_sequence(normalized)`:查同(项目,船,货,到站,序号)、**仅日期不同**、且 **dispatch_status 仍 OPEN** 的批次(NULL 安全比较)。
  - 新增 `_merge_suspected_date_misread()`:命中即把告警追加进 `tail_cargo_remark`(`疑似放货日期误读: 通知X vs 现存Y…`)+ WARN 日志,**不覆盖现存日期/数量、不建重复批**。
  - ingest 循环在 `get_by_batch_key` 未命中后插入该检测分支(误读→归并 continue,否则照常 INSERT)。
- **回归保护**:仅 OPEN 触发——旧批 `confirmed_received`(航次结束)后,新航次合法复用同序号 lot 照常建新批(5/11 与 5/29 双 lot01 不被误并)。
- **守门测试**(`tests/test_data_agent.py`):`test_misread_release_date_merges_into_open_same_sequence_lot`(误读归并、日期保留、告警可见)、`test_closed_lot_allows_legit_new_voyage_same_sequence`(closed 后合法双 lot01 各自成批)。全量 33 + 相关 94 绿。
- **残留**:仅 OPEN 重叠的罕见真航次重叠会被并(已打告警供人工核),符合既定取舍。
- **缺口2(中唐货运提取器)仍挂起**——需真实中唐发运群货运文本样本,见下;故本工单暂不归档。

## 缺口2:中唐特钢"货运信息"文本无自动提取器(B-中唐)
- **现状**:`extract_freight_detail`(`freight_detail_extractor.py:35`)强制 `订单标识 CGR\d+`,否则 no_match —— 这是**朝阳/吉林**格式(订单标识 CGR + 合同 HNMC)。中唐格式不同(合同 ZLDSZT、供方、到港船/进口船 import_ship_name、X港-Y港),走 `create_release_batch` 路,**补充货运消息→import_ship_name 的自动提取目前没有**(运达7 合并是人工)。吉林货运有测试(`test_freight_detail_*`),中唐 0 覆盖。
- **yaml 已要求**(zhongtang.yaml:32-33):补充货运消息船名≠出港通知单船名时,`import_ship_name <- 补充货运消息船名`。
- **待办**:需**真实中唐发运群货运文本样本**(几条)→ 照样本写中唐 freight 提取(或扩 extractor)→ 落 import_ship_name/合同/计划号 → 守门测试。

## 已覆盖(非缺口,审计确认)
- A1-船名误读(蓝鳍→蓝鲽):`canonicalize_ship_text` + `test_ocr_misread_ship_name_is_canonicalized_then_ingested`
- A2-不重复建批:`test_upsert_dedup`;第N批→lotN:`第N次下达计划→lotN` 正则 + 多测试
- A3-括号信息:`test_basic_remark` 等
- A4-特约事项→lot1:`test_single_release_plan_without_explicit_sequence_defaults_to_lot01`
- B-吉林货运:`test_freight_detail_text_patterns` / `test_freight_detail_extractor`
