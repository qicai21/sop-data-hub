# R67: Text Classification & Routing → message_inbox

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R66.1 (`6966dd9`)

---

## 1. 目标

不再依赖 executor_runner 里的硬编码 `"四平"` 判断。text 消息进入 message_inbox 后由 text_router 判断项目、flow、node，回写 message_inbox。

---

## 2. 新增/修改文件

| 文件 | 操作 |
|---|---|
| `src/ops_hub/sop/text_router.py` | 新增：text 消息分类路由 |
| `scripts/run_live_service.py` | 修改：集成 text_router 到 `process_event_once` |

---

## 3. Text Router 路由规则

### 优先级

| # | 规则 | project_id | flow | node |
|---|---|---|---|---|
| 1 | **发车文本**（目的地 + 道线/车数） | 按目的地映射 | `departure_flow` | `detect_departure_message` |
| 2 | **朝阳业务上下文**（船名/目的地/货品） | `chaoyang_steel` | `dispatch_flow` | `capture_business_context` |
| 3 | **货运信息**（结构化货名/合同/船名） | 按关键词推断 | `freight_detail_flow` | `enrich_release_batch` |
| 4 | **忽略短消息**（ok/好的/收到…） | — | — | `ignored` |
| 5 | **默认** | — | — | `ignored` |

### 发车文本要求

必须同时满足：
- 已知目的地关键词（四平/朝阳西/汐子）
- **且**有结构性信号：道线（煤六/十四道…）或车数（45节/装55节…）

防止 false positive：`"朝阳西，15997 吨，宝腾海，PB粉"` 有朝阳西但无车数/道线 → 不判定为发车文本。

---

## 4. 验收结果

### wx_2206 验收

```
text: 煤六 45节 四平铁 蓝鳍
↓
is_sop_msg: 1
sop_project_id: jilin_jingang_jinzhou
sop_flow: departure_flow
sop_node: detect_departure_message
processing_status: matched_sop
summary: [departure_text] 煤六 45节 四平铁 蓝鳍 →四平
```

**✅ 验收通过**

### 全量 text 消息路由结果

| message_id | group | 路由 |
|---|---|---|
| wx_2206 | 铁晟业务工作群 | **jilin_jingang / departure_flow / detect_departure_message** ✅ |
| wx_2339 | 铁晟业务工作群 | ignored（凌东铁 不在已知目的地） |
| wx_19 | 数据单发群 | freight_detail_flow / enrich_release_batch（鞍子河，项目待推断） |
| wx_25-35 | 数据单发群 | freight_detail_flow（马兰探险/丰收散运 货运信息） |
| wx_40 | 数据单发群 | chaoyang_steel / dispatch_flow / capture_business_context |
| wx_47 | 数据单发群 | jilin_jingang / freight_detail_flow（红土镍矿） |
| wx_50 | 数据单发群 | jilin_jingang / freight_detail_flow（印粉） |
| wx_52 | 数据单发群 | chaoyang_steel / dispatch_flow（朝阳西放货+木森17） |
| wx_53 | 数据单发群 | jilin_jingang / freight_detail_flow（印粉） |

**最终状态：** 12 matched_sop + 1 ignored = 13 text messages

---

## 5. 集成位置

`scripts/run_live_service.py` `process_event_once()`:

```
upsert_message_inbox_event(event)     ← R61
↓
classify_text_message(event)          ← R67 (新增)
↓
update_message_inbox_with_route(...)  ← R67 (新增)
↓
[existing processing flow...]
```

---

## 6. Commit

`src/ops_hub/sop/text_router.py` + `scripts/run_live_service.py` (modified)
