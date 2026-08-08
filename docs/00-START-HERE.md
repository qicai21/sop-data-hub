# sop-data-hub — 项目背景与架构(交接 / onboarding 唯一入口)

> 给"接手开发的 Claude(dispatch 模式)/ 新人 / 几个月没碰回来的我自己"看。
> 读完应能立刻动手做系统开发,不必从 git log 倒查。
> **最后更新：2026-08-08**（候选链路、Web 看板、九三循环追踪、费用迁移边界）。
> 配套:开发踩坑见 [`PITFALLS.md`](PITFALLS.md);业务规则细节见 [`business-rules/`](business-rules/);所有问题/变更走 [`issues/`](issues/) 工单。
>
> **维护闸**：这里只记心智模型、操作入口与铁律。新增 daemon、改变诊断入口、新增铁律或改变跨仓边界时必须更新本文件；纯 bug 修复只写工单和测试。正文控制在约 250 行，细节拆到相应文档。

---

## TL;DR(30 秒)

锦州港**铁路 + 海铁联运发运业务系统**（郭东北自用）。微信群消息（图片/文本）→ 自动识别 → 落业务实体（放货批次 / 车·箱 / 发运 Excel）→ 对外动作（发微信群 / 传收货人门户）→ 用铁路 **95306** 数据交叉印证。跟踪 **4 个 SOP 项目**，改动走 **工单 + 测试 + git**。

- **日常看板**：`http://<Mac 局域网 IP>:8765`，只读、无登录，仅允许 `10.1.2.0/24`。
- **开发/无网页备用**：`tmux attach -t board`（`scripts/cli_dashboard.py`）。
- **费用与结算**：在 sibling [`fee_manager`](../../fee_manager)；SOP 只维护运输事实，不在本仓新建费用工单。

---

## 1. 这是干啥的(业务背景)

把"港口/铁路发货全靠人盯微信群 + 手抄"自动化:
- **上游**:港口/工厂往微信群发**出港计划通知单**(图)、**检装车通知单**(图,车号→船/lot 权威)、**发运文本**("汐子铁 鞍子河 54 节")、**货运信息**等。
- **本系统**:识别 → 建/匹配**放货批次(release_batch)** → 从 95306 拉**真实发运车号**入库 → 生成**发运 excel** 发回群 / **上传收货人门户**(朝钢=鞍钢)。
- **95306**(中铁电子货运)= 发运的**权威事实源**(有哪些车/箱、货票号、到站、各车交付状态)。本系统不创造发运事实,只把"港方分船意图"和"95306 真实车"对齐。

**只跟踪有 yaml 的 4 个项目**(铁律,代码靠 `_agent_sop_authorized` 自动卡):
| 项目 | project_id | 业务形态 | 生命周期 mode |
|---|---|---|---|
| 吉林金钢(锦州港→四平) | `jilin_jingang_jinzhou` | 集装箱(箱级) | full_track_to_received |
| 中唐特钢 | `zhongtang_special_steel` | 整车(铁矿粉) | full_track_to_received |
| 朝阳钢铁(朝钢) | `chaoyang_steel` | 整车,**上传鞍钢门户** | shipped_is_completed(发运即完) |
| 九三大豆 | `jiusan` | 集装箱 + 散粮,**多船循环** | full_track_to_received |

其余项目(金通铜矿/草市鑫达/凌钢/乌钢…)的单子**一律放弃**,别扩大。

---

## 2. 端到端数据流(一张图记住)

```
微信群
  │  wechat-ops-agent:拉消息 + 解码图片(zstd/SQLCipher)→ 落 jsonl  (只产数据,不做业务)
  ▼
live-service:扫 jsonl → message_inbox + VLM(Qwen3.6@8021)分类 + 项目授权(_infer_sop_project_token)
  │            分类:出港计划通知单 / 检装车通知单 / 其它图;文本另走 text_router
  ▼
text-watch (--run-chains):inbox 文本/任务 → 跑 chain(发运/检验链)+ lifecycle closeout + verifier
  │   ├─ 出港计划通知单 → create_release_batch(建放货批次 lotN,按"第N次下达计划")
  │   ├─ 检装车通知单(图)→ 检验链:95306窗口反推 → 入库 wagon → 发运excel → (朝钢)上传鞍钢+反查
  │   ├─ 检装车文本 → 触发器,与通知单图 rendezvous(#143)
  │   └─ 中唐货运文本 → enrich 放货批次(计划号/合同号/品名)
  ▼
对外动作:发运 excel 发微信群([GROUP013]=数据单发群)/ 朝钢上传鞍钢门户(必紧跟反查)
  ▲
rail95306-sync:5min/轮同步 95306 → wagon 的车号/状态/货票号;调 pending_match_verifier 重试挂起候选
jiusan-sync / morning-reconcile / status-sync:九三集装箱/散粮按台账分船、晨报对账、状态与循环追踪(见 §7)
```

**控制边界**（见 §6）：链条可能真实发送微信、上传门户；诊断前先确认当前幂等键、目标群、输入范围及工单上下文。优先使用只读查询和隔离单函数，不能把“重跑”当作无副作用的默认诊断手段。

### 2.1 检装车候选链路（2026-07 起必读）

```text
检装车图(VLM) -> candidate
业务文本 -> rendezvous（按到站分段补船名/到站/实装数）
-> release_batch（顺序 lot 优先；lot10_a 先于 lot10_b）
-> 95306 时间窗恢复（单到站不误拆）
-> 过滤缺陷车/空排 -> ingest_wagons -> 当前批次范围的 Excel/上传
```

- 检装图权威源是 `GROUP001`（铁晟业务工作群）；**中唐特钢发运群仅接收货运信息和出港计划通知单，不作为检装图来源**。
- 关键模块：`inspection_text_rendezvous`、`inspection_window_recover`、`inspection_destination_split`、`inspection_candidate_batch`、`inspection_source_policy`、`text_station_segments`、`release_dispatch_priority`、`jilin_mixed_departure`。
- 吉林、朝钢、中唐均启用 yaml `sequential_lot_match_priority`：同船多 lot 必须按顺序唯一分配，不能随意挂到最近开放 lot。

---

## 3. 核心数据对象(库 `data/sop_agent.db`,SQLite)

| 表 | 是什么 | 关键字段 | 主键/唯一 |
|---|---|---|---|
| `release_batches` | **放货批次**(看板主行 = 项目+船+lot) | `id, project, ship_name, batch_sequence`(lot01..), `dispatch_status, notice_date, batch_date, destination_station, shipped/remaining_weight_tons, cargo_arrival_weight` | id |
| `wagon_shipments` | **整车/散粮**(车级) | `batch_id, car_no, ydid, ticketed_at, latest_stage_key, marked_weight, hph, cargo_name, ship_name, reconciled_at` | — |
| `wagon_container_shipments` | **集装箱**(箱级) | `batch_id, box_no(11位), ydid, hph(货票号), ticketed_at, latest_stage_key, ship_name, reconciled_at` | — |
| `container_loading_notice` / `bulk_loading_notice_wagon` | **分船台账**(港方货票/简装车通知单 → 哪票归哪船);**九三 sync 按它路由** | `ydid, box_no/car_no, ship_name, hph` | — |
| `inspection_ingestion_candidates` | 检装车通知单候选(图→车号→船) | `ship_name, candidate_status, source_image_path, release_batch_id, car_numbers_json, wagon_count` | — |
| `workflow_task_db` | 链/任务队列(pending/succeeded/failed/skipped) | `task_type, task_status, message_id, input_json, output_json` | — |
| `external_action_log` | 对外动作审计(发送/上传)+ 幂等键 | `action_type, action_status(planned/executed), idempotency_key` | idempotency_key |
| `reconcile_action_log` | 对账更正逐笔日志(reroute/gate/delete) | `action, entity_key, from_value, to_value` | — |

常见批次状态：`pending_freight`（有放货单、待货运信息）、`enriched`（货运信息已补全，尚未产生/归属发运事实）、`loading`、`all_loaded`、`tracking`、`delivered`、`confirmed_received`、`closed`。状态含义与实际 wagon 落库分开判断，不能把 `enriched` 当作“已入库”。

**唯一标识铁律**:
- **`ydid`(运单 id)= 每趟唯一**,是 95306 与本系统的统一键。**发运量必按 ydid 计数,绝不按 car_no/box_no**——循环车/箱号会反复复用(九三集装箱 3 列轮转、散粮循环车)。
- 集装箱箱号 `box_no` 必须 **11 位完整**(TBxU+7数字)。
- `marked_weight` = **标载 = 标重 = 计费重量**(同一属性,整车按它算)。rail 那列有噪声/缺失,故按车型推:C70系→70、C64系→61、C60/62系→60、散粮L系→60、平板/敞顶箱X70/NX70→64。

外部权威库(只读,**别写**):`rail95306-sync/runtime/95306_collection.sqlite3` 的 `shipments` 表(95306 全量:origin_name/destination_name/transport_mode_name/ticketed_at/status_name/latest_stage_key/container_numbers_json/raw_core_json 里的货票号 GZD…)。

---

## 4. Daemon 拓扑 + 重启逻辑(改代码必看)

| launchd Label | 类型 | 角色 | cwd |
|---|---|---|---|
| `…wechat-ops-agent` | 长驻 KeepAlive | 拉微信+解码图片→jsonl | wx-ops-agent |
| `…sop-data-hub.live-service` | 长驻 KeepAlive | jsonl→inbox + VLM分类 + 项目授权 + process_new_image | sop-data-hub |
| `…sop-data-hub.text-watch` | 长驻 KeepAlive | inbox/任务→**跑 chain(`--run-chains`)** + lifecycle closeout + verifier | sop-data-hub |
| `…sop-data-hub.dashboard-web` | 长驻 KeepAlive | 局域网只读货运 Web 看板，端口 8765 | sop-data-hub |
| `…rail95306-sync` | 长驻 KeepAlive | 5min 同步95306 + pending_match_verifier | rail95306-sync |
| `…sop-data-hub.jiusan-sync` | 周期 Interval | 九三集装箱/散粮按台账分船同步 | sop-data-hub |
| `…sop-data-hub.status-sync` | 周期 2h | `--apply --closeout --jiusan-cycle-tracking`；同步在途状态、关批和九三循环 | sop-data-hub |
| `…sop-data-hub.jiusan-morning-reconcile` | 定时 Calendar（8/9/10/11 点，成功一次即当日停止） | 九三晨报与内部沟通群对账 | sop-data-hub |
| `…sop-data-hub.jiusan-bulk-report-ingest` | 周期 30min | 九三散粮报表 ingest | sop-data-hub |

**⭐ 改了代码哪个要重启**:
- **长驻(KeepAlive)**:代码只在启动时加载 → 改了它跑的文件**必须** `launchctl kickstart -k`。判 stale = **进程启动时间 < 它跑的文件最后提交时间**(`ps -o lstart= -p <pid>` vs `git log -1 --format=%cd <file>`)。
- `dashboard-web` 仅允许配置的局域网网段访问、无登录；入口为 `http://<Mac局域网IP>:8765`。CLI/tmux 仅作开发与备用。
- **定时/周期(Interval/Calendar)**:每次起新进程、跑完就退 → **改了也不用重启**(下次定时自动 exec 新代码)。
- 文件→daemon 速查：`workflow_task_executor`/`executor_runner`/`pending_match_verifier`/`lifecycle_closeout`/`departure_excel`/`factory_verify`/`text_router` → **text-watch**；`runner.py`/`agent.py`/`classifier`/`image_route_promoter` → **live-service**（必要时 text-watch）；`dashboard_web.py` → **dashboard-web**；`sync_active_shipment_status.py` / `jiusan_cycle_tracking.py` → 等 **status-sync** 下轮执行；`reconcile/*` / `sync_jiusan_*` → 定时任务下轮执行。
- 重启:`launchctl kickstart -k "gui/$(id -u)/<Label>"`。**别用老的 nohup & disown**(脱离 launchd、Mac 重启即死)。

---

## 5. Lifecycle 状态机(批次)

```
pending_freight → enriched → loading → all_loaded → tracking → delivered → confirmed_received → closed
```
- 推进由 chain 各步 / `lifecycle_closeout`(text-watch 每轮扫)/ 手工信号驱动,`advance_lifecycle` 幂等+校验不倒退。
- **mode 分两类**(yaml `project_meta.lifecycle.mode`):
  - `shipped_is_completed`(朝钢):all_loaded 即 closed(发运即结算,不等到货)。
  - `full_track_to_received`(吉林/中唐/九三):走到 95306 全交付才 confirmed_received。
- **closeout 自动关批**:扫 active 批次,wagon **全交付**(`latest_stage_key∈{delivered,unloading_completed}`)→ 推 confirmed_received。散粮按计划吨位闸(欠装不关);集装箱按"全交付 + 最近2天无新车制票(静默)"关,避免趟间空档误关活跃船。**两张 wagon 表都数**。
- **"发完了"= 人工信号**:船发完但未必全交付时,标 `all_loaded`(排除出 loading,完成闸/sync 不再堆它),到货后 closeout 自动 confirmed。

---

## 6. 关键业务铁律(违反必出事)

1. **控制边界**：链条/触发 daemon 可能真实发微信、传门户。诊断先走只读查询和隔离单函数；若为修复而重跑，必须在工单中确认幂等键、目标群和影响范围。读链结果要逐字段看，别只看顶层。
2. **数据可重建,默认直接干**:除 rail95306 库外数据可重建,改数据默认 `apply=True` 直接做(改前备份 `data/sop_agent.db`),别堆 dry-run。但**对外动作(发微信/上传)红线**,不轻易重发(不幂等会对客户系统重复写)。
3. **发运量按 ydid**,不按车号/箱号(循环复用)。
4. **上传必紧跟反查**(朝钢鞍钢门户;[`business-rules/朝阳上传必须紧跟反查.md`](business-rules/朝阳上传必须紧跟反查.md))。反查口径 = **per-event present + unique**:本次上传的每个键(吉林=box_no/朝钢=car_no)都在门户返回里(missing==0)且各自唯一(duplicate==0)即通过;**不做整批对齐**(门户按计划号查必返整单全量累计 + 收货端偶发删数,total/extra 只观测)。
5. **项目授权靠内容锚**(`runner._infer_sop_project_token`):硬锚(到站/项目名)没命中时按 yaml `known_ships` 唯一命中兜底;新船要先在 yaml `project_meta.known_ships` 登记再放行。
6. **OCR 归一闭集**:VLM 误读走读时兜底(汐子←沙子/夕子/涉子;锦州新僡←锦州新德/得/儒;蓝鳍←蓝嶂),闭集随经验加。
7. **发运 excel = 一张检装车通知单(source_image)的数据展现**,全项目统一,一张单只发一次(幂等键=批次集+车数)。
8. **时间戳唯一入口** `sop_hub.utils.time.now_iso_beijing()`,绝不裸 `datetime.now()`/`datetime('now')`(UTC 会串)。
9. **可信分类不可被提取器静默二次拒绝**：已判为检装车的图，提取抖动必须形成可诊断候选/原因，不能无声丢弃。
10. **延迟候选保留原批次归属**：verifier 重试不能把既有候选改挂到其他 lot。
11. **缺陷车/空排不入实装**：`defect=true` 或空排不计入 wagon、Excel 或外部上传。
12. **通知单重发不洗已 enrich 的货运字段**：计划号、合同号、货物品名等已经确认的字段不能被重跑清空。
13. **吉林混列须显式拆分**：必须有完整车号/箱号分配和票簇，缺车号即失败待人工，不得伪报成功。

---

## 7. 对账与九三子系统

`src/sop_hub/reconcile/`:把"港方分船意图 vs 系统现状"自动对齐。**三源真值表**:
```
95306(全集/真实性)  ×  额外源(归属:港方货票/通知单)  ×  DB(现状)
```
- 引擎 `engine.py`:三方 join → 五分类(`ok / mismatch / missing / new_unattributed / phantom`)。**归属锚 = release_batch_id**(不是船名——船跨项目/lot 复用)。
- 每项目一个 `ReconcileSpec`(声明 universe/db_rows/source_batch/persist_to_ledger);九三 = 集装箱lot01/散粮lot02 两 leg。
- 更正(`--apply`):reroute 错挂(**改台账** `container_loading_notice`,否则被 sync 按台账 revert 回去!)+ 完成闸移新货到活跃船;**phantom 删 / new 分船不自动做,只告警**;逐笔日志。
- 核对完毕状态(`reconciled_at`):每日只算未核对的;历史船 grandfather 封存。
- **关键教训**：reconcile 改 wagon 表会被按台账路由的 sync revert → 必须改台账。

九三已是独立的活跃子系统，不是普通 batch 列表：

| 能力 | 入口/口径 |
|---|---|
| 分船同步 | `jiusan-sync`，按台账分船 |
| 晨报与内部群对账 | `jiusan-morning-reconcile`；每日 8/9/10/11 点，成功一次停止 |
| 在途/关批/循环更新 | `status-sync` 每 2 小时，使用 `jz-port-super` 综合账号进行 95306 四探针抽样 |
| 箱池核算 | `jiusan_cycle_pool` |
| 看板循环 | 港口空箱 → 港口重箱 → 在途（重） → 新台子站 → 三三零专用线 → 在途（返空） |
| 散粮报表 | `jiusan-bulk-report-ingest` |

循环状态按每列抽样车体轨迹推断，属于运营观测；发运/归属事实仍以 `ydid` 和台账为准。

---

## 8. 开发工作流(怎么干活)

1. **所有变更立工单**：`docs/issues/<日期>-工单-xxx.md`，现象→根因→改动→验证→执行收尾。工单状态、根目录/`archived/` 纪律以 [`issues/README.md`](issues/README.md) 为准；已完成立即归档。
2. **测试守门**：`PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`。测试数以 `pytest --collect-only` 实测为准，不能把文档中的历史数字当口径；历史事故必须沉淀为回归测试。
3. **git 单分支 main**:2026-06-29 起收敛到单一 `main`(已删 codex/老分支)。在 main 上干,改完提交(commit 尾 `Co-Authored-By: Claude …`),用户授权才 push。
4. **改完按 §4 重启对应 daemon**;改链 → text-watch;改 agent/runner → live-service+text-watch。
5. **代码地图（按改动目标）**：

   | 要动什么 | 先看 |
   | --- | --- |
   | 分类、项目授权、OCR 归一 | `runner.py`、`classifier/`、`data_agent/agent.py` |
   | 检装候选、挂起与恢复 | `inspection_*`、`pending_match_verifier`、`text_station_segments`、`text_router` |
   | lot 优先级、吉林混列 | `release_dispatch_priority`、`jilin_mixed_departure`、`config/project_sops/*.yaml` |
   | 入库、Excel、微信发送 | `wagon_ingest.py`、`workflow_task_executor.py`、`executor_runner.py`、`departure_excel.py` |
   | 朝钢门户上传与反查 | `factory_verify.py`、`external/chaoyang_ansteel/` |
   | 生命周期与状态同步 | `lifecycle.py`、`lifecycle_closeout.py`、`active_status_sync.py` |
   | 九三对账、循环与箱池 | `reconcile/`、`jiusan_cycle_tracking.py`、`jiusan_cycle_pool.py` |
   | Web 看板 | `scripts/dashboard_web.py`、`scripts/cli_dashboard.py` |
6. **费用边界与联动**：
   - SOP 是运输事实源：`release_batches`（含批次、`cargo_arrival_weight`）、`shipment_release_batch_matches`、`wagon_shipments`、`wagon_container_shipments` 及台账归属。
   - `fee_manager/data/fee_ledger.db` 是费用与结算事实源：费目/合同费率、`fee_batch`、`fee_record`、`settlement_plan`、发票和正式单证。
   - 同步方向**仅 SOP → fee_manager**。任何放货批次、到厂重量、`ydid`/箱级归属的修正，先在 SOP 校正并验证，再执行 fee_manager source sync；不得只在费用库补运输事实，更不得反向写回 SOP。
   - 同步入口：`cd ../fee_manager && PYTHONPATH=src .venv/bin/python scripts/sync_source_tables_from_sop.py`。该命令备份费用库、同步运输/参考表，并保护费用侧本地表；生成结算单前还要检查 source sync 的时间与结果。
   - 改运输表结构、批次口径、到厂重量含义或同步映射时，SOP 与 fee_manager 分别建工单、分别加回归测试；费用新工单只在 `fee_manager/docs/issues/`。
7. 诊断/看板（只读优先）：Web 看板 `http://<Mac局域网IP>:8765`；备用 `tmux attach -t board`；`sqlite3 data/sop_agent.db` 直查；`pending_match_verifier --once` 看挂起候选。重跑链条前遵守 §6.1 的影响确认。

---

## 9. 不要做的事

- ❌ 把重跑链条/触发 daemon 当作无副作用诊断；未确认幂等、目标群和范围不得执行。
- ❌ 写 95306 库;给已发完的船堆新货(完成闸);手搓 INSERT wagon(走 `ingest_wagons`)。
- ❌ reconcile 只改 wagon 表不改台账(被 sync revert)。
- ❌ 裸 `datetime.now()` / UTC 时间戳;按车号/箱号计发运量。
- ❌ 处理 4 个 yaml 项目以外的单子;给 agent.py 加项目硬编码(走 yaml)。

---

## 10. 自测考题(读完上面,试着答这 7 题)

> 答完交给郭东北核对掌握程度。

**Q1.** 系统统计某船"发了多少车/箱"时,为什么必须按 `ydid` 计数、而不能按 `car_no`/`box_no`?哪类业务最容易踩这个坑?

**Q2.** 你想确认"某条发运链到底跑没跑通",于是打算把那条 chain 重新触发一遍看结果。这样做有什么严重后果?正确的诊断方式是什么?

**Q3.** 对账引擎是哪三个数据源在比对?各自提供什么?为什么"归属"用 `release_batch_id` 而不用船名?如果 reconcile 只改了 `wagon_shipments.batch_id`(没改台账)会发生什么?

**Q4.** 朝钢/吉林上传后"反查"原来用"整批对齐"(total==expected 且 extra==0)判成败,为什么这是错的?现在的 present+unique 口径具体校验什么(键是什么)?

**Q5.** 你改了 `lifecycle_closeout.py` 和 `sync_jiusan_all.py`。`lifecycle_closeout` 同时由 text-watch 每轮和 `status-sync --closeout` 调用：哪个 daemon 必须重启以立即加载、哪个等下轮即可？你用什么判据决定一个长驻 daemon 是否在跑旧代码？

**Q6.** 某批次的到厂重量或 lot 归属修正后，为什么不能只改 `fee_ledger.db`？正确的 SOP 与 fee_manager 修复顺序是什么？

**Q7.** 检装候选已挂到 lot10_a，verifier 重试时能否改挂 lot10_b？一张已确认 enrich 的批次遇到重复通知单后，哪些字段不得被清空？
