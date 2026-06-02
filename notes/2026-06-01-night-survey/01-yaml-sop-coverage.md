# 01 — YAML SOP 流程解析覆盖率审计

调查日期: 2026-06-01 晚
调查者: Claude (Opus 4.7 1M)
范围: `config/project_sops/{jilin_jingang,chaoyang,jiusan,zhongtang}.yaml` 与 `src/sop_hub/` Python 解析层
约束: **只读审计**,不动代码、不动 yaml

---

## 0. 执行摘要 (30 秒读完)

**当前 yaml 驱动度约 30–40%。** 真正"读 yaml 字段、按 yaml 行为"的代码只有 4 类:
(1) `listening_tasks` (sop_watcher → monitoring_plan_compiler);
(2) `shipped_weight_rule` 的 calc DSL — **唯一真 yaml-driven 的业务规则**;
(3) `flows.*.output_templates.departure_excel` (departure_excel.py);
(4) `flows.departure_flow.factory_upload_config` (factory_upload.py 仅吉林金钢);
其余 70% — 文字识别、项目识别、流程路由、task_type 分发、tracking 状态映射、报告投递、放货匹配优先级、freight_detail 抽取 — 全都是 Python 硬编码 if/elif 链。

**最严重的 3 个 gap (按业务影响排序):**
1. **`workflow_task_store._resolve_task_type`** — 整套"yaml flow → 哪个 Python 执行函数"的分发完全在 5 条硬编码 if 里。新项目想加流程,必须同时改这里 + `workflow_task_executor._execute_*`,yaml 完全不参与决策。
2. **`runner._infer_sop_project_token` + `monitoring_plan_matcher._fallback_alignment_match` + `text_router._infer_project_from_text` + `departure_text_parser._DESTINATION_PROJECT`** — 4 个独立的 Python 文件各自维护"什么关键词 → 哪个项目"的硬编码字典。yaml 里 `project_meta.cargo_names` / `destination_station` 这些声明性字段完全没人读。Claude 每次新增船名/到站时被迫在 4 个地方同步打补丁。
3. **task_resolver、tracking_flow、release_notice_flow、inspection_notice_flow.steps[*].action、exceptions、acceptance、scope、entities、states、archive、idempotency** — 这些大块 yaml **完全没有任何 Python 在读**。`sop_task_compiler` 会把它们编译成 ExecutableTask 但只是给人看的清单,不会被执行器消费。yaml 写了等于没写。

**结论:用户原话"对 yaml 的 sop 流程解析不完全"的"完全"应理解为 yaml ≈ 文档 + 一小部分模板** — yaml 在系统里更多是 "认证白名单 + 一份给人/给 AI 看的业务说明书",而不是 "可执行 SOP"。

---

## 1. yaml 顶层结构清单 (字段 → 解析状态矩阵)

### 1.1 jilin_jingang.yaml (665 行,最完整)

| yaml 字段 | Python 读? | 读它的文件 | 实际行为 |
|---|---|---|---|
| `project_id` | ✅ 完全 | `models/project_sop.py:load_project_sop`, `data_agent/agent.py:active_business_sop_project_tokens`, `runner.py:_active_project_sop_tokens`, `sop/shipped_weight.py:_scan_project_yamls`, `sop/departure_excel.py:_find_yaml_for_project`, `sop/factory_upload.py` | 项目认证 token,DB 写库白名单 |
| `project_name` | ✅ | 同上 | 同 project_id |
| `status: active` | ✅ | 上述各处 `if sop.status == "active"` | 白名单门禁 |
| `version` | ❌ | — | 装饰字段 |
| `source_of_truth` | ❌ | `sop_watcher.py` 写日志时引用一次,**不消费** | 装饰字段 |
| `sop_type: ordinary_freight` | ❌ | — | 装饰字段 |
| `project_meta.category` | ❌ | — | 装饰字段 |
| `project_meta.dashboard` | ❌ | — | 装饰字段 (dashboard_intent.py 自己又写了一份硬编码 PROJECT_DASHBOARD 表) |
| `project_meta.origin_station` | ❌ | — | 装饰字段 (executor_runner.py 硬编码 origin="高桥镇") |
| `project_meta.destination_station` | ❌ | — | 装饰字段 (executor_runner.py 硬编码 dest="四平") |
| `project_meta.cargo_names` | ❌ | — | 装饰字段 (`runner._infer_sop_project_token` 自己另搞一套关键词) |
| `project_meta.has_inspection_slip` | ❌ | — | 装饰字段 |
| `project_meta.departure_confirmation_source` | ❌ | — | 装饰字段 |
| `project_meta.shipped_weight_rule.per_wagon` | ✅ **完全** | `sop/shipped_weight.py:_load_project_rule` → `calc/interpreter.py:evaluate` | **真 yaml-driven** (R76) — 见第 4 节 |
| `scope` (含 description / excludes) | ❌ | — | 装饰字段,纯文档 |
| `groups` | ❌ | — | 装饰字段 (具体 group 信息靠 `listening_tasks` 重复一份) |
| `freight_detail_text_patterns` | ❌ | — | 装饰字段 (`text_router._FREIGHT_STRUCTURED_KEYWORDS` 在 py 里自己写了一份) |
| `listening_tasks[*].group_id/group_name/wxid` | ✅ | `models/project_sop.py:load_project_sop` → `sop/sop_watcher.py:_project_sop_yaml_to_compiler_input` | 给微信 watcher 用 |
| `listening_tasks[*].listen_options` | ✅ | 同上 | 给 watcher 用 |
| `listening_tasks[*].pull_image` | ✅ | 同上 | 给 watcher 用 |
| `listening_tasks[*].routing[*].message_type` | ✅ 部分 | sop_watcher 转 monitoring entry | image/text 区分 |
| `listening_tasks[*].routing[*].trigger_condition` | ⚠️ **半读半弃** | sop_watcher 跳过 `match_departure_text_template/match_text_template/always` 三种值,把它们扔给 `_fallback_alignment_match` 的硬编码关键词处理 | 见 gap-B |
| `listening_tasks[*].routing[*].target_node` | ✅ 字面值 | sop_watcher 拼接到 target_sop_node | 但下游 `workflow_task_store._resolve_task_type` 不消费这个值,它另起一套 if 链 |
| `listening_tasks[*].routing[*].text_patterns` | ✅ | `RoutingRule.text_patterns` → sop_watcher entry | watcher 内部使用 |
| `listening_tasks[*].routing[*].supplemental` | ❌ | — | 装饰字段 |
| `sources.wechat_chat_records/wechat_images/sop_agent_db/railway_95306` | ❌ | — | 装饰字段 (sop_agent.db 路径硬编码在 `runner.py`/`config.py`,95306 路径硬编码在 4 个 .py 里) |
| `entities.release_batch.uniqueness/rules/fields` | ❌ | — | 装饰字段 (`batch_key` 在 `agent.py:1317-1335` 硬编码用 `ship_name+cargo_name+destination+batch_date_key+seq`) |
| `entities.wagon_shipment.uniqueness` | ❌ | — | 装饰字段 |
| `states.release_batch[*]` | ❌ | — | 装饰字段 (实际状态值在 `agent.py` 硬编码: in_progress/completed/suspended/review_needed/cancelled) |
| `states.wagon_shipment[*]` | ❌ | — | 装饰字段 |
| `flows.release_notice_flow.steps[*]` | ❌ | — | **装饰字段** — `sop_task_compiler` 会编译成 ExecutableTask 但只生成"清单",从不被任何执行器调用 |
| `flows.release_notice_flow.steps[*].conditions.any_keywords` (四平/吉林金钢) | ❌ | — | 装饰字段 (`runner._infer_sop_project_token` 自己又写了 `("四平",)` 硬编码) |
| `flows.freight_detail_flow.text_patterns` | ❌ | — | 装饰字段 (`freight_detail_extractor` 用自己的正则,`text_router` 用自己的关键词集合) |
| `flows.freight_detail_flow.extract_fields` | ❌ | — | 装饰字段 (`freight_detail_extractor` 硬编码 `_RE_ORDER_ID/_RE_CONTRACT_NO/_RE_CARGO_NAME/_RE_QUANTITY/_RE_PORT`) |
| `flows.freight_detail_flow.binding_rule: manual_only` | ❌ | — | 装饰字段 (`extract_freight_detail` 硬编码 `binding_status="needs_manual_binding"`) |
| `flows.departure_flow.message_patterns` (regex 列表) | ❌ | — | 装饰字段 (`departure_text_parser` 用自己的 `_RE_LANE/_RE_CAR_COUNT` 正则 + `_DESTINATION_MAP` dict) |
| `flows.departure_flow.examples` | ❌ | — | 纯文档 |
| `flows.departure_flow.text_semantics` | ❌ | — | 纯文档 |
| `flows.departure_flow.steps[*].action` | ❌ | — | 装饰字段 (`workflow_task_executor._execute_jljg_departure` 硬编码调用顺序) |
| `flows.departure_flow.steps[*].query_window` (`message_time - 60m`) | ❌ | — | 装饰字段 (`executor_runner` 硬编码 `window_before_minutes=720, window_after_minutes=720`,**值都不一致**!) |
| `flows.departure_flow.steps[*].duplicate_policy` | ❌ | — | 装饰字段 (`create_wagon_shipments` 硬编码 idempotent skip) |
| `flows.departure_flow.output_templates.departure_excel.workbook/columns/file_naming/style` | ✅ **完全** | `sop/departure_excel.py:_load_template` | **真 yaml-driven** (R78) |
| `flows.departure_flow.factory_upload_config.endpoint/login_path/upload_path/payload_fields` | ✅ **完全** | `sop/factory_upload.py:_load_factory_config` | **真 yaml-driven** (R54) — 但只 `jilin_jingang.yaml` 一份,凭"路径常量"读 |
| `flows.departure_flow.factory_upload_config.auth.username` | ⚠️ 读 token_header,不读 username | `factory_upload.py` 硬编码 `FACTORY_USERNAME="saibin"` + `FACTORY_PASSWORD="Xts@95306"` | 半读半弃 (用户授权) |
| `flows.departure_flow.factory_upload_config.excel_to_factory_mapping` | ⚠️ 加载到 dataclass,**未消费** | `_load_factory_config` 读到字段里,但 `build_upload_payloads` 没使用 | 死代码读取 |
| `flows.departure_flow.factory_upload_config.verify.endpoint/params/check` | ❌ | — | 装饰字段 (`factory_verify.py` 自己硬编码 URL + check 逻辑) |
| `flows.departure_flow.delivery_targets.dev` (郭东北) | ❌ | — | 装饰字段 (`executor_runner.py:369` 硬编码 `target="郭东北"`) |
| `flows.departure_flow.steps[*].runtime_rules.test_mode` | ❌ | — | 装饰字段 |
| `flows.tracking_flow.trigger/steps/tracked_status` (发车/到站/交付) | ❌ | — | 装饰字段 (`shipment_status_sync.py` 自己硬编码 `STATUS_FIELD_MAP`) |
| `flows.tracking_flow.steps[*].conditions.95306_status` | ❌ | — | 装饰字段 |
| `flows.tracking_flow.steps[*].action` | ❌ | — | 装饰字段 |
| `runtime.watcher.enabled/hot_reload` | ❌ | — | 装饰字段 (`sop_watcher` 自己决定) |
| `runtime.sop_reload` | ❌ | — | 装饰字段 |
| `runtime.idempotency.message.unique_key` | ❌ | — | 装饰字段 (`message_inbox` 表 schema 硬编码 UNIQUE) |
| `runtime.idempotency.wagon_shipment.unique_key` | ❌ | — | 装饰字段 (`wagon_shipments` UNIQUE 在 SQL DDL 里) |
| `runtime.task_resolver.{departure_excel,factory_json,...}.task_type` | ❌ | `sop_task_compiler` 编译到 ExecutableTaskPlan,但不被执行器调用 | 装饰字段 (`workflow_task_store._resolve_task_type` 走自己的 if 链) |
| `exceptions[*].name/action` | ❌ | — | 装饰字段,纯文档 |
| `archive.matched/unmatched.{images,json}.path` | ❌ | — | 装饰字段 (`runner._move_processed_artifacts` 拼自己的路径 `business/projects/<project>/<destination>/<ship>/<lot>/`) |
| `acceptance[*]` | ❌ | — | 纯文档 |

**实读 / 装饰 比例约 12 / 50。** 12 个被读、约 50 个块/字段被忽略。

### 1.2 chaoyang.yaml (306 行)

| yaml 字段 | Python 读? | 行为 |
|---|---|---|
| `project_id` / `project_name` / `status` | ✅ | 同 jilin |
| `version` / `sop_type` / `source_of_truth` | ❌ | 装饰 |
| `project_meta.category/dashboard` | ❌ | 装饰 |
| `project_meta.destination_station: 朝阳西` | ❌ | 装饰 (`departure_text_parser._DESTINATION_PROJECT` 把 "朝阳西" → "chaoyang_steel" 写在 py 里) |
| `project_meta.cargo_names` (铁矿/铁矿粉/朝阳西铁/朝阳西铁矿) | ❌ | 装饰 (`runner._infer_sop_project_token` 自己写了 `("合远9","朝阳钢铁","朝钢","朝阳西","朝阳铁")`) |
| `project_meta.has_inspection_notice` | ❌ | 装饰 |
| `project_meta.confirmed_received_rule.default` | ❌ | 装饰 (`shipment_status_sync` 自己硬编码 95306 交付→confirmed) |
| `project_meta.release_batch_policy.do_not_merge_release_batches` | ⚠️ **注释里提到** | `agent.py:1245-1260` 在注释里说"尊重 chaoyang.release_batch_policy.do_not_merge_release_batches",**但代码并不真读这个 yaml 字段**,而是按 `notice_date` scope dedup,正好碰巧一致 | 巧合一致 |
| `project_meta.shipped_weight_rule.per_wagon` (lookup + coalesce + lookup_by_prefix + to_float) | ✅ **完全** | `shipped_weight.py` + `calc/` | **真 yaml-driven** |
| `groups.{primary,test,production_report}` | ❌ | 装饰 (`listening_tasks` 里又重复一份) |
| `listening_tasks[*]` (一般字段) | ✅ | 与 jilin 一致 |
| `flows.release_notice_flow.steps[*]` | ❌ | 装饰 |
| `flows.release_notice_flow.steps[*].conditions.any_keywords` (朝阳西/朝钢/朝阳钢铁) | ❌ | 装饰 |
| `flows.release_notice_flow.steps[*].match_fields` | ❌ | 装饰 (`agent._query_existing_batches_like` 硬编码 ship/cargo/dest) |
| `flows.release_notice_flow.steps[*].rule.same_ship_new_notice_create_new_batch/do_not_merge_release_batches` | ❌ | 装饰 |
| `flows.inspection_notice_flow.steps[*]` | ❌ | 装饰 — 但被映射到 `chaoyang_inspection_chain` task_type (硬编码) |
| `flows.inspection_notice_flow.steps[*].action: match_release_batch_by_ship_destination_cargo` | ⚠️ **action 名匹配代码函数名** | yaml 里写的 action 字符串 `match_release_batch_by_ship_destination_cargo` 跟 `sop/match_release_batch.py` 的函数同名,但 yaml 没驱动调用 — 是 `workflow_task_executor._execute_chaoyang_inspection_chain` 硬编码 import 调用 | 名义对齐,实际硬编码 |
| `flows.inspection_notice_flow.steps[*].rule.prefer_open_batch_same_ship_destination_cargo` | ⚠️ **代码实现了规则,但不读 yaml** | `match_release_batch.py` 硬编码 `_OPEN_STATUSES=("in_progress","suspended")` + `_STATUS_PRIORITY` | 行为巧合一致 |
| `flows.inspection_notice_flow.steps[*].rule.multiple_candidates: pending_review` | ⚠️ 代码硬编码同样行为 | `match_release_batch.py` 返回 `reason="multiple_candidates"` | 行为巧合 |
| `flows.inspection_notice_flow.steps[*].rule.fallback_window_minutes: 60` | ❌ | 装饰 (`workflow_task_executor._execute_chaoyang_inspection_chain` 不用窗口,直接按车号查 95306) |
| `flows.inspection_notice_flow.steps[*].rule.auto_apply_when_count_matches_and_no_conflict` | ❌ | 装饰 (chain 直接全插) |
| `flows.business_text_flow.steps[*].rule.text_is_context_only` | ❌ | 装饰 |
| `flows.tracking_flow.*` | ❌ | 装饰 |
| `flows.report_delivery_flow.output_templates.departure_excel.workbook/columns` (5 列简版) | ✅ | `departure_excel.py:_FLOW_KEYS_TO_PROBE=("departure_flow","report_delivery_flow")` 两个 flow 都试 | 真 yaml-driven |
| `flows.report_delivery_flow.steps[*]` | ❌ 大部分 | — |
| `flows.report_delivery_flow.steps[*].target_contact: [郭东北]` (test) | ✅ | `workflow_task_executor._resolve_send_target` 读 `send_test_report.target_contact[0]` | 真 yaml-driven (R78b) |
| `flows.report_delivery_flow.steps[*].target_group: [GROUP004]` (production) | ✅ | 同上 fallback | 真 yaml-driven |
| `runtime.idempotency.release_batch.unique_key` | ❌ | 装饰 (实际 batch_key 在 `agent.py` 里硬编码) |
| `runtime.task_resolver.*` | ❌ | 装饰 |
| `exceptions.*` | ❌ | 装饰 |
| `acceptance[*]` | ❌ | 纯文档 |

### 1.3 jiusan.yaml (74 行)

| yaml 字段 | Python 读? | 行为 |
|---|---|---|
| `project_id: jiusan` | ✅ | 项目白名单 |
| `project_name` / `status` | ✅ | 同上 |
| `rail95306.account/cargo_name/primary_routes/supplemental_routes` | ❌ | **完全无消费者** — jiusan 项目代码侧根本没分支 |
| `contract.*` (合同号/双方/价格/三条路线) | ❌ | **完全无消费者** |
| `listening_tasks[*]` | ✅ | 一般字段同上 |
| `listening_tasks[*].routing[*].target_node: archive_for_manual_release_setup` | ❌ | 装饰 — 没有任何 Python 节点叫这个名字 |
| `listening_tasks[*].routing[*].trigger_condition: manual_designation_required` | ❌ | 装饰 |
| `listening_tasks[*].routing[*].save_db: true` | ✅ | `RoutingRule.save_db` 在 model 里被解析,但下游消费点要核实 |

**结论:jiusan.yaml 几乎完全是文档,系统上线后等于没接入。**

### 1.4 zhongtang.yaml (74 行)

| yaml 字段 | Python 读? | 行为 |
|---|---|---|
| `project_id: zhongtang_special_steel` | ✅ | 白名单 |
| `project_name` / `status` | ✅ | — |
| `listening_tasks[*]` | ✅ | 一般字段 |
| `listening_tasks[*].routing[*].target_node: create_release_batch / process_business_image / process_inspection_slip` | ⚠️ | target_node 字面值在 `agent.py:_BUSINESS_NODE_NAMES` 集合里判定 SOP 是否"业务"(详见 gap-D) |
| `listening_tasks[*].routing[*].report_targets.{dev,production}` (郭东北/GROUP003) | ⚠️ 模型解析,**当前没人调用** | `RoutingRule.report_targets` 字段加载到内存,但 `workflow_task_executor._resolve_send_target` 只读 `flows.report_delivery_flow.steps[*].target_contact` — zhongtang 没声明 report_delivery_flow,所以这里读不到 | 死字段 |

**结论:zhongtang 项目唯一 yaml-driven 的部分是"我存在,我是 active",其他业务逻辑全部走 `agent.py:release_batch_sop_project` / `runner._infer_sop_project_token` 里的中文关键词硬编码。**

---

## 2. 硬编码黑名单 (Python 反向证据)

以下每一条都是"代码硬编码了 yaml 本应表达的事实"。

### 2.1 项目识别 / SOP 白名单关键词 (4 处重复,危险)

| 位置 | 硬编码内容 | yaml 本应表达 |
|---|---|---|
| `src/sop_hub/runner.py:730-744` (`_infer_sop_project_token`) | `if any(token in text for token in ("合远9","朝阳钢铁","朝钢","朝阳西","朝阳铁")): return "朝阳钢铁铁矿发运项目"` 等三条 | `project_meta.cargo_names` + 一个新增的 `project_meta.aliases` 字段 |
| `src/sop_hub/data_agent/agent.py:118-128` (`release_batch_sop_project`) | 同上 `if chaoyang in tokens and any(anchor in text for anchor in ("合远9","朝阳钢铁","朝钢","朝阳西","朝阳铁"))`;中唐用 `("中唐","赤峰中唐","ZLZT")`;还有特殊 case `record.ship_name == "贝拉" and destination == "汐子"` | 同上 |
| `src/sop_hub/sop/text_router.py:25-48` (`_DEST_PROJECT`, `_DEPARTURE_*`, `_CHAOYANG_SHIP/DEST/CARGO_KEYWORDS`) | `_CHAOYANG_SHIP_KEYWORDS = {"木森17","合远9","宝腾海","贝拉"}` 等 | 同上 |
| `src/sop_hub/sop/departure_text_parser.py:30-63` (`_KNOWN_SHIPS`, `_DESTINATION_MAP`, `_DESTINATION_PROJECT`) | `_KNOWN_SHIPS = {"长航滨海","蓝鳍","木森17",...}` + `_DESTINATION_PROJECT = {"四平":"jilin_jingang_jinzhou", ...}` | 同上 |
| `src/sop_hub/sop/monitoring_plan_matcher.py:92-110` (`_fallback_alignment_match`) | 按 group_id 写死 if 链:`if group_id == "GROUP003": if any(token in event_text for token in ("汐子","放货","实装")): project_id="zhongtang_special_steel"` | yaml 已有 `listening_tasks` 但路由匹配没读 |

**4–5 个文件维护"船名/到站/合同关键词 → project"的硬编码字典 — 这是 Claude 每次新业务都要打补丁的根因。** yaml 里 `chaoyang.cargo_names: ["铁矿","铁矿粉","朝阳西铁","朝阳西铁矿"]` 早写好了,但没人读。

### 2.2 task_type 分发 — `workflow_task_store._resolve_task_type` (单点硬编码)

`src/sop_hub/sop/workflow_task_store.py:80-98`:

```python
def _resolve_task_type(project_id: str, flow_name: str, node_name: str) -> str:
    if (project_id == "jilin_jingang_jinzhou"
            and flow_name == "departure_flow"
            and node_name == "detect_departure_message"):
        return "jljg_departure_text_chain"
    if (project_id == "chaoyang_steel"
            and flow_name in ("inspection_flow", "inspection_notice_flow")
            and node_name in ("detect_inspection_notice",
                              "extract_inspection_notice",
                              "extract_inspection_notice_fields")):
        return "chaoyang_inspection_chain"
    if node_name == "create_release_batch":
        return "create_release_batch"
    if project_id == "chaoyang_steel" and flow_name == "dispatch_flow":
        return "chaoyang_dispatch_context"
    if flow_name == "freight_detail_flow":
        return "freight_detail_enrichment"
    return "generic_sop_task"
```

**这是整个系统最关键的"yaml flow → executor"分发表 — 全是 5 条 if/elif,没有任何 yaml 参与。** yaml 里 `runtime.task_resolver.{name}.task_type` 早已声明了映射(`departure_excel: excel_generation` 等),但这里完全不读。

下游执行器 `workflow_task_executor.py:149-179`:

```python
if task_type == "jljg_departure_text_chain":
    result = _execute_jljg_departure(...)
elif task_type == "create_release_batch":
    result = _execute_create_release_batch(...)
elif task_type == "chaoyang_inspection_chain":
    result = _execute_chaoyang_inspection_chain(...)
elif task_type in ("chaoyang_dispatch_context","freight_detail_enrichment","generic_sop_task"):
    result = {"action":"skipped","status":"skipped","output_json":{"reason":...}}
else:
    result = {"action":"failed","status":"failed","error_message":f"unknown task_type"}
```

**每个 task_type 一个独立 `_execute_*` Python 函数。新项目加流程 = 加新 task_type = 改 store.py + 改 executor.py + 写新 `_execute_*` — yaml 一改也不解决问题。**

### 2.3 executor_runner 的 jilin_jingang 特化 (架构性硬编码)

`src/sop_hub/sop/executor_runner.py` 整个文件号称 "executor runner" 但 docstring 第 8 行明说:

```
- Only jilin_jingang_jinzhou departure_flow.
```

行 248-251:
```python
elif candidate.project_id != "jilin_jingang_jinzhou":
    preview.skipped_reason = f"not jilin_jingang (project_id={candidate.project_id})"
```

行 269-270:
```python
origin = "高桥镇"
dest = candidate.destination or "四平"
```

行 278-279:
```python
window_before_minutes=720,
window_after_minutes=720,
```
yaml 里 `departure_flow.steps[*].query_window` 明明写了 `±60m`,这里硬编码 `720m=12h`,**值不一致,且 yaml 不被读**。

行 369:
```python
target = "郭东北"
```

行 408-409 在 `run_departure_executor_chain_if_applicable` 中:
```python
if "四平" not in event.text:
    return None
```
门禁直接判 "四平" 字面值。

### 2.4 factory_upload.py — 半 yaml-driven 但路径硬编码

`src/sop_hub/sop/factory_upload.py:46`:
```python
SOP_YAML_PATH = REPO_ROOT / "config" / "project_sops" / "jilin_jingang.yaml"
```
**字面写死 jilin_jingang.yaml**。换个项目,这个文件就不能用。`_load_factory_config(project_id="jilin_jingang_jinzhou")` 的 project_id 参数是装饰,实际不影响路径。

行 41-42:
```python
FACTORY_USERNAME = "saibin"
FACTORY_PASSWORD = "Xts@95306"
```
yaml 里 `factory_upload_config.auth.username` 有 saibin,代码也读了 `token_header`,但 username + 密码用 Python 常量。

行 263-277 的 `for fdef in config.fields` 循环里,字段 source 解析也是硬编码 5 个 if:
```python
if fdef.source == "fixed": ...
elif fdef.source == "95306_confirm": ...
elif fdef.source == "release_batch.cargo_name": ...
elif fdef.source == "release_batch.ship_name": ...
elif fdef.source == "release_batch.factory_contract_no": ...
elif fdef.source == "release_batch.order_identifier": ...
```
新增 source 类型必须改代码 — 这违反 calc DSL 的"加 op 不改 core"原则。

### 2.5 95306 状态映射 — `shipment_status_sync.py:27-44`

```python
STATUS_FIELD_MAP = {
    "发车": ["departed_at"], "已发车": ["departed_at"],
    "到站": ["departed_at","arrived_at"], "已到站": [...],
    "交付": ["departed_at","arrived_at","delivered_at"],
    "货物已交付": [...], "已交付": [...]
}
STATUS_TO_DISPATCH_STATUS = {"交付":"delivered", "货物已交付":"delivered", ...}
```

`flows.tracking_flow.tracked_status: ["发车","到站","交付"]` 早在 jilin yaml 写了,但 sync 代码自己又写了一份扩展集合(含 "已" 前缀变体)。yaml 与代码二选一,目前实际是"代码说了算"。

### 2.6 release_batch 匹配优先级 (`match_release_batch.py:44-45`)

```python
_OPEN_STATUSES = ("in_progress", "suspended")
_STATUS_PRIORITY = {"in_progress": 1, "suspended": 2}
```

chaoyang.yaml `inspection_notice_flow.steps[match_release_batch].rule.prefer_open_batch_same_ship_destination_cargo: true` 表达的"优先 open batch"行为是巧合一致 — 代码本身就这么干,但根本不读 yaml。

更糟糕的是 `executor_runner.py:174-181` 里另写了一套优先级 (`CASE dispatch_status WHEN 'in_progress' THEN 0 WHEN 'active' THEN 1 ELSE 2 END`),含 'active' 一档 — 两套优先级口径不一致。

### 2.7 dashboard_intent / report_intent 的项目硬编码字典

`src/sop_hub/sop/dashboard_intent.py:17-21`:
```python
PROJECT_DASHBOARD = {
    "zhongtang_special_steel": "中唐特钢",
    "chaoyang_steel": "朝阳钢铁",
    "jilin_jingang_jinzhou": "吉林金钢",
}
```
yaml `project_meta.dashboard: "normal_freight"` 不读;`project_name` 不读;自建一份映射。

`src/sop_hub/sop/report_intent.py:20-39`:
```python
PROJECT_REPORT_CONFIG = {
    "zhongtang_special_steel": {
        "template_path": "/Users/qicai21/.../ztsteel_departure_report_template.xlsx",
        "recipient_target": DEV_RECIPIENT_TARGET,  # {"type":"contact","name":"郭东北"}
        ...
    },
    "chaoyang_steel": {...},
    "jilin_jingang_jinzhou": {...},
}
```
yaml `delivery_targets.dev` (jilin) 或 `report_delivery_flow.target_contact` (chaoyang) 不读;硬编码 absolute path + 郭东北。

### 2.8 archive path 不读 yaml — `runner._move_processed_artifacts`

`config/project_sops/jilin_jingang.yaml:640-654` 声明:
```yaml
archive:
  matched:
    images:
      path: >
        projects/jilin_jingang_jinzhou/
        <destination>/<ship_name>/lot<order_identifier>/images/
    ...
```

`src/sop_hub/runner.py:377`:
```python
base = root / "business" / "projects" / _sanitize_component(parts["project"]) / _sanitize_component(parts["destination"]) / _sanitize_component(parts["ship"]) / parts["lot"]
image_dir = base / "images" / date_text
```

**代码自己拼路径,yaml 完全不读。路径结构都不完全一致(yaml 是 `lot<order_identifier>`,代码用 `parts["lot"]` 自己提取)。**

### 2.9 idempotency.unique_key 不读 yaml

`config/project_sops/jilin_jingang.yaml:598-606`:
```yaml
runtime.idempotency.message.unique_key: [group_id, seq]
runtime.idempotency.wagon_shipment.unique_key: [wagon_no, waybill_no, release_batch_id]
```

实际 `data_agent/db.py` 的 wagon_shipments DDL 用的 UNIQUE 是不同字段;`workflow_task_db` 用的是 `(message_inbox_id, task_type)` (`workflow_task_store.py:52`),也不在 yaml。

### 2.10 freight_detail extract_fields 不读 yaml

yaml: `flows.freight_detail_flow.extract_fields: [order_identifier, contract_no, cargo_name_detail, port, quantity_tons]`

代码 `freight_detail_extractor.py:34-51` 硬编码 5 个正则。yaml 改字段顺序、删字段、加字段,代码都不知道。

### 2.11 task_resolver 不读 yaml

yaml: `runtime.task_resolver.departure_excel.task_type: excel_generation` 等

代码 `sop_task_compiler.py:121-126`:
```python
_TASK_TYPE_MAP: dict[str, str] = {
    "departure_excel": "excel_generation",
    "factory_json": "json_generation",
    "telegram_delivery": "telegram_delivery",
    "receiver_upload": "http_delivery",
}
```
**代码里 Python 常量 _TASK_TYPE_MAP 跟 yaml 里 task_resolver 各自维护一份** — 两个独立的真理源。

### 2.12 _BUSINESS_NODE_NAMES 硬编码

`data_agent/agent.py:61-69`:
```python
_BUSINESS_NODE_NAMES = {
    "create_release_batch","process_inspection_slip","process_business_image",
    "detect_release_notice","detect_inspection_notice",
    "detect_departure_message","enrich_release_batch",
}
```
判断 SOP 是不是"业务 SOP"用的白名单。yaml 加新 node 必须同步改这里。

### 2.13 departure 文字时间窗口值不一致

yaml `departure_flow.steps[build_95306_query_window].query_window.start: "message_time - 60m"`

代码 `executor_runner.py:278`: `window_before_minutes=720`

**实际跑的是 12 小时窗口,yaml 写的是 1 小时。**

### 2.14 95306 DB 路径 (rail95306) — 4 处硬编码

```
src/sop_hub/sop/create_wagon_shipments.py:163
src/sop_hub/sop/workflow_task_executor.py:425
src/sop_hub/sop/query_95306_shipments.py:40
src/sop_hub/data_agent/dispatch_board.py:685
```
每处都拼 `~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3`。yaml `sources.railway_95306.path` 字段不被读。

---

## 3. 真正 yaml-driven 的子系统 (完整清单)

| 字段 | 代码 | 覆盖范围 |
|---|---|---|
| `project_id` / `project_name` / `status: active` | `models/project_sop.py`, `agent.py`, `runner.py`, `shipped_weight.py` | 4 个项目 |
| `listening_tasks` (group_id/wxid/listen_options/routing.message_type/text_patterns) | `models/project_sop.py:load_project_sop` → `sop_watcher._project_sop_yaml_to_compiler_input` → `monitoring_plan_compiler` | 4 个项目,但 trigger_condition 半弃 |
| `project_meta.shipped_weight_rule.per_wagon` | `sop/shipped_weight.py` + `calc/` DSL | 仅 chaoyang + jilin 声明,jiusan/zhongtang 没声明 |
| `flows.{departure,report_delivery}_flow.output_templates.departure_excel` | `sop/departure_excel.py` (R78 完全 yaml 驱动,包括 cell_format/constant/file_pattern/style) | 仅 chaoyang + jilin 声明 |
| `flows.departure_flow.factory_upload_config.{endpoint,login_path,upload_path,payload_fields}` | `sop/factory_upload.py:_load_factory_config` | **仅 jilin**(路径硬编码读这一个 yaml) |
| `flows.report_delivery_flow.steps[send_{test,production}_report].target_contact[0]/target_group[0]` | `sop/workflow_task_executor.py:_resolve_send_target` | **仅 chaoyang**(其他项目没声明 report_delivery_flow 这个 flow_name) |

**总共 ≈ 6 个字段族真被解析驱动业务行为。**

---

## 4. R76 calc DSL — 真 yaml-driven 的样板

这是整个仓库里**最值得借鉴**的设计。代码:
- `src/sop_hub/calc/interpreter.py` — 50 行,负责递归 evaluate + 字段引用 `$wagon.car_model`
- `src/sop_hub/calc/registry.py` — 30 行,扫描 `ops/` 和 `sources/` 子目录自动注册
- `src/sop_hub/calc/ops/{multiply,lookup,lookup_by_prefix,coalesce,to_float,sum_over}.py` — 每个 op 一个文件
- `src/sop_hub/calc/sources/wagon_shipments_in_release_batch.py` — 数据源,只一个

执行入口 `src/sop_hub/sop/shipped_weight.py:_load_project_rule`:
```python
return (sop.get("project_meta", {}) or {}).get("shipped_weight_rule")
```
然后 `compute_for_release_batch` 把 yaml 表达式包成 `sum_over` 跑。

### 它真的工作吗?

**真的工作。** 证据:
- `chaoyang.yaml:25-43` 写了完整的嵌套表达式 (`lookup → coalesce → to_float / lookup_by_prefix → table`,含 `on_miss: pending_review`)
- `jilin_jingang.yaml:29-33` 写了简单的 `multiply: $wagon.cargo_count × 32.3`
- `sum_over.execute` 会 captured `basis_per_item` 写回 wagon 级 `weight_rule_basis` 字段做审计
- `shipped_weight.py` 的 PRAGMA 检查显示它会同步刷新 `release_batches.shipped_weight_tons / remaining_weight_tons / unresolved_wagon_count / actual_wagon_count`

### 关键设计原则 (其他子系统应学)

1. **加新 op = 加新 .py 文件** — `registry._scan` 自动发现,没 if 链。
2. **op 之间不互相 import** — 全靠 `evaluate(child, ctx)` 递归。
3. **op 有显式 `on_miss`** — 不返回崩溃,返回 `pending_review` 字符串,被上游 `sum_over` 计数。
4. **数据源也是注册表** — `wagon_shipments_in_release_batch` 跟 op 一样自动扫,新加 source 也是放文件。
5. **yaml 字段对齐 dataclass / DB 列名** — `$wagon.car_model`、`$wagon.cargo_count`、`$wagon.marked_weight` 直接 mapping 到 `wagon_shipments` 表的列。

### 唯一限制

- 表达式只能算"per_wagon"语义 — 还没扩展到 per_release_batch / per_voyage。
- yaml 字段路径固定 `project_meta.shipped_weight_rule.per_wagon`,不支持多规则共存。
- jiusan / zhongtang 还没声明 `shipped_weight_rule`,这两个项目就拿不到自动算重量。

**如果整个 yaml SOP 都按这个范式重写一遍 — 把"action 名"、"trigger_condition"、"output_template"、"task_resolver"、"匹配规则"等都做成 op + 注册表,Claude 就不再需要每次打补丁。**

---

## 5. 一份按 yaml 块算的 "驱动度" 总账

| 大类 | 总声明字段数(估) | 实读 | 实读率 |
|---|---|---|---|
| 项目元数据 (project_id/name/status) | 12 | 12 | 100% |
| project_meta.shipped_weight_rule | 6 | 6 | 100% (calc DSL) |
| listening_tasks (group/wxid/listen/routing) | ~60 | ~30 | 50% |
| flows.*.steps[*] (含 action/conditions/match_fields/rule) | ~150 | 0 | 0% |
| flows.*.output_templates.departure_excel | ~30 | ~30 | 100% (R78) |
| flows.departure_flow.factory_upload_config | ~25 | ~20 | 80% (仅 jilin) |
| flows.report_delivery_flow.send_*.target_* | 4 | 4 | 100% (R78b,仅 chaoyang) |
| flows.tracking_flow | ~20 | 0 | 0% |
| runtime.* (watcher/sop_reload/idempotency/task_resolver) | ~25 | 0 | 0% |
| sources / entities / states / scope / exceptions / acceptance / archive | ~60 | 0 | 0% |
| jiusan rail95306/contract/pricing_routes | ~30 | 0 | 0% |
| zhongtang report_targets | ~10 | 0 (模型层加载到内存但下游不消费) | 0% |
| **总计 (粗估)** | ~432 | ~102 | **≈ 24%** |

**实际"被代码读 + 影响业务行为"的字段数约 24%。** 加权"业务关键性",真正驱动业务流程的 yaml 块 ≈ 6 个 (见 §3),其余 70%+ 是文档 + 半文档。

---

## 6. yaml ↔ 代码"重复定义"清单 (两个真理源问题)

每条都意味着 yaml 改了不生效,必须同步改 py:

1. **destination → project 映射**
   - yaml: `chaoyang.project_meta.destination_station: 朝阳西`
   - py 重复: `departure_text_parser._DESTINATION_PROJECT` / `text_router._DEST_PROJECT` / `runner._infer_sop_project_token`

2. **cargo_names**
   - yaml: `chaoyang.project_meta.cargo_names: [铁矿,铁矿粉,朝阳西铁,朝阳西铁矿]`
   - py 重复: `runner._infer_sop_project_token` 的 `("合远9","朝阳钢铁","朝钢","朝阳西","朝阳铁")` + `text_router._CHAOYANG_CARGO_KEYWORDS`

3. **groups & wxid**
   - yaml: `chaoyang.groups.primary.wxid: 18919596289@chatroom` + `listening_tasks[0].wxid` 重复
   - py: 通过 `ListeningTask` 读,但 `monitoring_plan_matcher._fallback_alignment_match` 又按 `group_id == "GROUP001"` 写死

4. **task_type 命名**
   - yaml: `runtime.task_resolver.departure_excel.task_type: excel_generation`
   - py: `sop_task_compiler._TASK_TYPE_MAP = {"departure_excel":"excel_generation",...}` (相同内容,各写一份)

5. **tracked_status**
   - yaml: `tracking_flow.steps[track_95306_status].tracked_status: [发车,到站,交付]`
   - py: `shipment_status_sync.STATUS_FIELD_MAP` 扩展到 7 个 key

6. **shipped_weight_rule.car_model lookup table**
   - yaml: `{C70:70, C64:61, C62:60}` + `{70:69.5, 61:63, 60:62}`
   - py 旁路: 没有重复 — 这是好的样板 (calc DSL 真的读 yaml)

7. **factory_upload payload field mapping**
   - yaml: `factory_upload_config.payload_fields.wagonNumber.source: 95306_confirm`
   - py: `factory_upload.py:263-277` 解释 source 字符串走 5 个 if

8. **window minutes**
   - yaml: `query_window.start: "message_time - 60m"`
   - py: 硬编码 720

9. **excel_to_factory_mapping**
   - yaml: `excel_to_factory_mapping: {合同号:contractNumber, ...}`
   - py: 加载到 `FactoryUploadConfig.excel_to_factory` 但 **完全不消费**

10. **archive paths**
    - yaml: `archive.matched.images.path: projects/jilin_jingang_jinzhou/<destination>/<ship_name>/lot<order_identifier>/images/`
    - py: `runner._move_processed_artifacts` 拼成 `business/projects/<project>/<dest>/<ship>/<lot>/images/<date_text>/`

11. **report 模板路径**
    - yaml: jilin 无声明
    - py: `report_intent.py:35` 硬编码 absolute path

12. **rail95306 db 路径**
    - yaml: `sources.railway_95306.path: 95306_collection.sqlite3`
    - py: 4 个 .py 都拼 `~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3`

13. **release_batch dedup 字段**
    - yaml chaoyang: `runtime.idempotency.release_batch.unique_key: [project_id, ship_name, notice_date, cargo_name, quantity_tons]`
    - py: `agent.py:1317-1335` 用 `ship_name + cargo_name + destination + batch_date_key + sequence` (字段都不一致)

14. **freight_detail_text_patterns**
    - yaml jilin: `freight_detail_text_patterns: [标识号,订单标识,订单号,合同号,合同,货名,品名,详细货名,矿种,批次]`
    - py: `text_router._FREIGHT_STRUCTURED_KEYWORDS = {合同号,入场合同号,...}` (内容稍有差异)

15. **departure 文字 message_patterns**
    - yaml jilin `departure_flow.message_patterns`: 5 个 .* regex
    - py: `departure_text_parser._RE_LANE/_RE_CAR_COUNT/_DESTINATION_MAP` 自成体系,逻辑结构都不同

---

## 7. 子图 — 一次"完整业务请求"的 yaml 触达情况

以"朝阳钢铁检装车通知单 → 出 excel"流程为例,看每一步**实际**读 yaml 还是代码硬编码:

| 步骤 | 流向 | yaml 触达? |
|---|---|---|
| 1. 微信群图片进 | wx-ops-agent | yaml `listening_tasks` 决定订阅(✅ 读) |
| 2. 图片分类为"检装车通知单" | `classifier/` | 不读 yaml,模型决定 |
| 3. VLM 抽取 → JSON | `engines/inspection_slip.py` | 不读 yaml |
| 4. SOP 项目认证 | `runner._ensure_sop_project` | ❌ 不读 yaml,用硬编码关键词 ("合远9","朝阳西") 推断 |
| 5. `inspection_ingestion_candidates` 入表 | `agent.ingest_inspection_payload` | ❌ 不读 yaml |
| 6. message_inbox 加 task 行 | `workflow_task_store.create_task_from_message_inbox` | 调用 `_resolve_task_type` → ❌ 5 条 if |
| 7. 分发到 `chaoyang_inspection_chain` 执行器 | `workflow_task_executor._execute_chaoyang_inspection_chain` | ❌ 硬编码 import + 调用顺序 |
| 8. 用 ship+dest+cargo 找 release_batch | `match_release_batch.py` | ❌ 不读 yaml,内置 `_OPEN_STATUSES` |
| 9. 按车号查 95306 拿运单 | `workflow_task_executor.py:526-536` 硬编码 SQL | ❌ 不读 yaml |
| 10. INSERT wagon_shipments | 直接 SQL | ❌ |
| 11. 算 shipped_weight | `shipped_weight.compute_for_release_batch` | ✅ **唯一读 yaml 的步骤** (R76 calc) |
| 12. 生成 departure excel | `departure_excel.generate_departure_excel` | ✅ 读 yaml `output_templates.departure_excel` |
| 13. send_excel via wx-ui-bridge | `workflow_task_executor._resolve_send_target` + `send_excel.send_to_wechat` | ✅ 读 yaml `report_delivery_flow.steps[send_*_report].target_contact` (R78b) |
| 14. 标记 candidate matched | 直接 UPDATE | ❌ |

**14 步里 3 步真读 yaml (步 1, 11, 12, 13)** → 路径覆盖 ≈ 25%。

---

## 8. 建议优先级 (修哪 3 个,yaml 驱动度跃升)

只罗列影响范围 — 不给方案。

### P0 — `workflow_task_store._resolve_task_type` 改为 yaml 驱动

如果让 yaml 的 `runtime.task_resolver.{name}.task_type` 直接被这里消费,那么:
- 新加一个项目 / 新加一个 flow 不再需要改 store.py 和 executor.py 的 5 条 if
- yaml 真正成为"flow → executor"的路由表
- 所有 4 个项目都受益(目前 jiusan/zhongtang 拿不到 task_type 全部走 generic_sop_task,完全空转)

### P1 — 项目识别关键词唯一真理源 (`project_meta.cargo_names` + 新增 `aliases`)

如果让 `runner._infer_sop_project_token` / `agent.release_batch_sop_project` / `text_router._infer_project_from_text` / `departure_text_parser._DESTINATION_PROJECT` 共享一份从 yaml `project_meta.cargo_names + ship_aliases + destination_station` 读的字典,那么:
- Claude 不再需要每次新船名/到站时改 4 处
- 用户每次新业务只改 yaml,不打补丁
- gap-A 那 4 处重复的硬编码字典消失

### P2 — `_execute_*` 重构成"action 注册表"(仿 calc DSL)

把 `workflow_task_executor._execute_jljg_departure / _execute_chaoyang_inspection_chain / _execute_create_release_batch` 拆成 yaml `flows.*.steps[*].action` 字符串 → action 注册表里的 handler 的映射 (类似 `calc/registry.py` 那种自动扫目录),那么:
- yaml `action: ["classify_message","extract_release_notice_json"]` 真正变成可执行
- 加新 action = 加新 `actions/<name>.py` 文件,不改 dispatcher
- jiusan 的 `archive_for_manual_release_setup` 这种装饰节点也能直接落地一个 handler

### 次级

- P3: `archive.matched.images.path` 模板用 yaml,删 `runner._move_processed_artifacts` 的硬编码
- P4: `tracking_flow.tracked_status` 真驱动 `shipment_status_sync.STATUS_FIELD_MAP`
- P5: `factory_upload.SOP_YAML_PATH` 改成按 project_id 找 yaml,撤销对 `jilin_jingang.yaml` 的字面依赖
- P6: `excel_to_factory_mapping` 要么消费要么删除(目前是死代码)

---

## 9. 附录 — 一处特别诡异的发现

`config/project_sops/jilin_jingang.yaml:558` 的 `tracking_flow` 块和 `chaoyang.yaml:175` 的 `tracking_flow` 块在 yaml 里**完全没人读**,但 Python 里的 `shipment_status_sync.py` 实际跑的就是这两个 flow 描述的逻辑 — 行为一致是因为**两边都按业务事实写的,不是因为 yaml 驱动了代码**。

这是典型的"用户/Claude 看 yaml 觉得已经写好了,跑出来好像也对 — 实际上没接通"的状态。一旦改 yaml(例如 `tracked_status` 删掉"发车"),代码继续按内置 `STATUS_FIELD_MAP` 跑,**没有任何 error / warning**。这是隐性最危险的 gap,因为它会让人产生"yaml 真的在驱动"的错觉。

---

调查结束。代码与 yaml 均未改动。
