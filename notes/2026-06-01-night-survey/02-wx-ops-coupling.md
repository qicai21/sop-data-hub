# wx-ops-agent → sop-data-hub 跨仓耦合调查

> 调查时间：2026-06-01 夜  
> 调查范围：`wx-ops-agent` 主分支 HEAD（含今早 commit `f42d394`），`sop-data-hub` 主分支 HEAD  
> 目的：搞清楚 wx-ops-agent 为什么要 `import sop_hub.*`、这种耦合是怎么长出来的、怎么干净地砍掉

---

## 1. 执行摘要（30 秒读完）

- **现状**：`wx-ops-agent` 在 4 个运行时入口（daemon 启动 → tracker 规则加载 → 图片归档 → 文本归档）直接 `sys.path.insert(0, /Users/qicai21/projects/repos/sop-data-hub/src)` 然后 `from sop_hub.config import load_settings`、`from sop_hub.runner import process_new_image`、`from sop_hub.data_agent.agent import BusinessDataAgent`，**同进程**调用 sop-data-hub 的业务逻辑。
- **起因**：2026-04-23 commit `4fc4e16 "Route wx images through ops-data-hub"` 是这条跨仓 import 的诞生点。当时 wx-ops-agent 是项目里第一个跑起来的进程，sop-data-hub 还没有 daemon，所以"图片落地 → 分类 → 抽取"被设计成由 wx 同进程拉起 ops_hub 的 runner。文本路由是 5 月（`5637d44`、`c94307d`）追加的同样模式。
- **为什么没解决**：今早 `f42d394` 只是 `ops_hub → sop_hub` 的字符串替换（跟仓库改名），并没有触碰耦合结构。`integrations/sop_data_hub.py` 还在用 `sys.path` 黑魔法把 sibling 仓库塞进 Python path。
- **解耦可行性**：**完全可行**。sop-data-hub 在 2026-05-26 就已经实现了 `sop_hub.sop.source_watcher.WxOpsSourceWatcher`，它是 _read-only_ 直接扫 `wx-ops-agent/data/chat_records/**/*.jsonl` + `~/Documents/bussiness-artifacts/wechat_images`，并能产出 `MessageEvent`；2026-05-31 又落了 `message_inbox` 表（`7ff8c94`）。也就是说，"wx 落盘 → sop-data-hub 自己消费"的目标架构在 sop-data-hub 这边已经先到位了，wx 这边还在用 4 月份的旧调用路径硬连。
- **推荐**：方案 B（"watcher 拉模式 + wx-ops-agent 只落盘"），可分两步交付：先剥离文本路由（最简单），再剥离图片分类/抽取（重头戏）。
- **关键观察**：`wx-ops-agent/src/wechat_ops_agent/ocr/business_group/` 这套分类器/pipeline 跟 sop-data-hub 的 classifier 实际上是 **同一份业务逻辑的两份实现**，当前都还活着，是技术债的根源。

---

## 2. 耦合点清单（5 个文件，6 处 import）

> 全部命中 `grep -rn "from sop_hub\|import sop_hub" /Users/qicai21/projects/repos/wx-ops-agent/src/` 的输出。

### 2.1 `src/wechat_ops_agent/integrations/sop_data_hub.py`（耦合中枢）

文件长度 84 行，是今早 `f42d394` 修过的那个。两个公开函数 + 一个 `_ensure_sop_hub_importable()` 路径注入器。

| 行号 | 调用 | 用途 |
|---|---|---|
| 12–15 | `_ensure_sop_hub_importable()`：`sys.path.insert(0, get_sop_data_hub_src())` | 把 `/Users/qicai21/projects/repos/sop-data-hub/src` 塞进 Python path，让 `from sop_hub.*` 能解析 |
| 30 | `from sop_hub.config import load_settings` | 拿到 sop-data-hub 的 `Settings` 实例 |
| 31 | `from sop_hub.runner import process_new_image` | **真正的业务调用**：分类 + 归档 + 按需抽取一条龙 |
| 67 | `from sop_hub.config import load_settings` | 文本路径同上 |
| 68 | `from sop_hub.data_agent.agent import BusinessDataAgent` | 创建 BusinessDataAgent 直接调 `ingest_business_text(text)` |

公开函数：

- `process_image_via_sop_data_hub(image_path, month_str, group_name, ...)` → 返回 `ProcessingResult`
- `process_text_via_sop_data_hub(text, group_name)` → 返回 `{"status", "record_count", "record_ids"}`

注意第 36–37 行干了一件**很恶心**的事：调用方在每次执行时通过 `settings.wechat_images_dir = str(wechat_root)` / `settings.extraction_output_dir = ...` **覆盖 sop-data-hub 的运行时配置**。这意味着 wx-ops-agent 不只是 import，还在动态改写对方的 `Settings`。

### 2.2 `src/wechat_ops_agent/chat_records/media_resolver.py`（媒体处理触发点）

```
11: from wechat_ops_agent.integrations.sop_data_hub import process_image_via_sop_data_hub, process_text_via_sop_data_hub
116: processed = process_image_via_sop_data_hub(image_path, month_str=month_str, group_name=group_name)
178: processed = process_text_via_sop_data_hub(text, group_name=group_name)
```

`resolve_media_paths()`（line 203）是 daemon 主循环每收到一条新消息**都会调一次**的入口（见 daemon.py line 514）。它负责：

1. 把微信 `.dat` 解码成 jpg（`decode_dat_to_jpg`）  
2. 落到 `~/Documents/bussiness-artifacts/wechat_images/<group>/<月>/<local_id>_<md5>.jpg`  
3. **立即** 调 `_classify_and_archive_image()` → 同步调 `process_image_via_sop_data_hub()`
4. 根据返回值（category="出港计划通知单"/"检装车通知单" 等）把图片再次 `shutil.move` 到 `{group}/{category}/`

文本同理：`_classify_and_route_text()` → `process_text_via_sop_data_hub()`，每条新文本消息都同步进 sop-data-hub 的 `BusinessDataAgent`。

### 2.3 `src/wechat_ops_agent/tracking/handlers/business_image_router.py`（旧业务群兜底）

```
7:   from wechat_ops_agent.integrations.sop_data_hub import process_image_via_sop_data_hub
251: ops_result = process_image_via_sop_data_hub(decrypted_img, month_str=..., group_name=canonical_name, force_extract=False)
```

文件头部第 22–32 行注释自己写了：

> [COMPATIBILITY RETAINED] 旧版业务群图片识别入口，代理内含业务判断的最后残留。  
> 此 Handler 应该仅作为单纯的 hook 调用器…它内置的 `business_strategies` 等逻辑是为了保证在完全迁移到 ProjectSOP 前，现存的中唐特钢链路不至于崩溃的临时向下兼容实现。

也就是说：**这个文件作者自己都已经标了"应该删"**。它内部还在用 `wechat_ops_agent/ocr/business_group/` 那套 OCR pipeline（`BusinessGroupImagePipeline` / `load_business_group_strategies`），和 sop-data-hub 的分类器是双轨。

### 2.4 `src/wechat_ops_agent/tracking/tracker.py`（规则加载）

```
67-69:
    from wechat_ops_agent.integrations.sop_data_hub import _ensure_sop_hub_importable
    _ensure_sop_hub_importable()
    from sop_hub.config import load_settings
    hub_settings = load_settings()
    ...
    rules["listener_objects"] = hub_settings.monitored_groups
    rules["sop_route_contexts"] = _sop_contexts_from_tracking_tasks(tracking_tasks)
```

`TrackerEngine` 启动时把 sop-data-hub 的 `monitored_groups` / `tracking_tasks` 当作**权威配置源**。注释（line 65）写的是：

> "这里确立了唯一的业务权威：所有的业务配置由 ops-data-hub 根据 ProjectSOPs 计算得出。"

设计意图很清楚：群组监听清单只能在 sop-data-hub 的 `config/project_sops/*.yaml` 里定义，wx-ops-agent 只是消费者。但实现方式选了"同进程读 settings"而不是"通过文件/HTTP 拿到列表"，导致 wx 在 daemon 启动就被强绑定。

### 2.5 `src/wechat_ops_agent/cli/main.py`（CLI 默认参数）

```
489-509: cmd=run-daemon
    from wechat_ops_agent.integrations.sop_data_hub import _ensure_sop_hub_importable
    _ensure_sop_hub_importable()
    from sop_hub.config import load_settings
    hub_settings = load_settings()
    if args.interval == 300 and hub_settings.daemon_interval:
        daemon_interval = hub_settings.daemon_interval
    ...
```

`run-daemon` 子命令在启动前会去问 sop-data-hub 拿 `daemon_interval` / `daemon_message_limit` / `tracking_rules_path` 三个参数。这只是 CLI 参数 fallback，重要性低，但**是同一个 sys.path 注入的延伸**。

### 2.6 `src/wechat_ops_agent/storage/paths.py`（路径解析时的递归）

```
17-23: get_wechat_image_root()
    from wechat_ops_agent.storage.paths import get_sop_data_hub_src  # ← 自引用导入
    src = str(get_sop_data_hub_src())
    if src not in sys.path:
        sys.path.insert(0, src)
    from sop_hub.config import load_settings
    settings = load_settings()
    if settings.wechat_images_dir:
        return Path(settings.wechat_images_dir).expanduser()
```

最离谱的一个点：**只是为了拿"微信图片落地目录"路径**，就跨仓 import 一次 `sop_hub.config`。`paths.py` 第 17 行的 `from wechat_ops_agent.storage.paths import get_sop_data_hub_src` 还是模块内自引用（其实就是同文件 38 行的函数），多余的一行。

---

## 3. 历史溯源

### 3.1 出生：commit `4fc4e16`，2026-04-23 17:42

```
Author: qicai21
Date:   Thu Apr 23 17:42:58 2026 +0800
    Route wx images through ops-data-hub

 src/wechat_ops_agent/integrations/__init__.py      |  1 +
 src/wechat_ops_agent/integrations/ops_data_hub.py  | 41 +++++++++
 src/wechat_ops_agent/storage/paths.py              | 31 +++++++
 src/wechat_ops_agent/tracking/handlers/business_image_router.py | 75 ++
 src/wechat_ops_agent/chat_records/media_resolver.py             |  5 +-
```

初始的 `ops_data_hub.py` 只有一个函数 `process_image_via_ops_data_hub(image_path, ...)`，41 行，干的事跟今天的版本一模一样：

```python
def _ensure_ops_hub_importable() -> None:
    src = str(get_ops_data_hub_src())
    if src not in sys.path:
        sys.path.insert(0, src)

def process_image_via_ops_data_hub(image_path, ...):
    _ensure_ops_hub_importable()
    from ops_hub.config import load_settings
    from ops_hub.runner import process_new_image
    settings = load_settings(config_path)
    wechat_root = str(get_wechat_image_root())
    settings.wechat_images_dir = wechat_root
    settings.classified_output_dir = wechat_root
    settings.extraction_output_dir = str(Path(wechat_root) / "extractions")
    settings.db_95306_path = str(get_95306_db_path())  # ← 这行后来被移除
    ...
    return process_new_image(resolved_image, settings, force_extract=force_extract)
```

**当时的设计意图**（推断自代码 + commit message）：
- 4 月份 wx-ops-agent 已经是一个独立 daemon，能拉到微信图片
- 当时 ops-data-hub（现 sop-data-hub）刚开始有 `runner.process_new_image`，没有自己的 daemon
- 最快接通的方式：让 wx 同进程把对方拉起来跑，避免 IPC 设计

### 3.2 加文本路由：`5637d44` + `c94307d`，2026-05-11 / 2026-05-15

```
5637d44 Enable SOP-gated text release ingestion       2026-05-11
c94307d Forward text releases to ops data hub          2026-05-15
```

`process_text_via_ops_data_hub` 在这里添上，模式抄图片那个：sys.path 注入 + 直接 import `BusinessDataAgent` 同进程跑。

### 3.3 中间还长出过 inspection 钩子，又被砍掉：`56a1791`，2026-05-14

```
56a1791 Remove wx-side inspection SOP business hooks
```

这个 commit 删了 `process_inspection_via_ops_data_hub` / `trigger_departure_report` / `integrations/inspection_sop.py`（69 行），共减少 448 行 / 增加 45 行。说明历史上**还有过更深的耦合**（甚至生成发车报告也是 wx 端发起调用），后来撤了。但**核心的 image + text hook 没动**。

### 3.4 sop-data-hub 侧的"反向"准备：`9edc9c6`，2026-05-26

```
9edc9c6 feat: add wx ops source watcher       2026-05-26 10:01
```

sop-data-hub 这边自己写了 `WxOpsSourceWatcher`，read-only 直接扫 `wx-ops-agent/data/chat_records/**/*.jsonl`、`~/Documents/bussiness-artifacts/wechat_images`、`wx-ops-agent/data/runtime-logs/daemon-auto.log`。注释（source_watcher.py line 1-9）写得非常清楚：

> This module stays local-only:
> - read chat records from `data/chat_records/**/*.jsonl`;
> - resolve image paths under `~/Documents/bussiness-artifacts/wechat_images`;
> - inspect daemon logs under `data/runtime-logs/daemon-auto.log`;
> - emit `MessageEvent` objects with stable message_id and preserved metadata;
> - **do not touch runtime, database, or wx-ops-agent.**

5 天后（`7ff8c94`，2026-05-31）又加了 `message_inbox` 表 + DAO + CLI。**这就是目标架构在 sop-data-hub 这边的落地状态**。

### 3.5 改名：`f42d394`，2026-06-01 21:33（今天早上）

```
f42d394 fix: sop_data_hub bridge import 跟随 sop-data-hub 仓库 ops_hub→sop_hub 重命名
```

只改了字符串：`ops_hub → sop_hub` × 4 个文件 = 6 处 import 语句。**结构毫无改动**。

### 3.6 时间线总结

| 日期 | 仓库 | 事件 |
|---|---|---|
| 2026-04-23 | wx | `4fc4e16` — sys.path 跨仓 import 诞生（图片） |
| 2026-05-09 | wx | `38f0cb3` — 修路径契约 |
| 2026-05-11 | wx | `52d3c72` — 图片 status artifact 暴露 |
| 2026-05-11 | wx | `5637d44` — SOP gating 文本 |
| 2026-05-14 | wx | `56a1791` — **砍掉** inspection / departure_report hook |
| 2026-05-15 | wx | `c94307d` — 文本路由完整接通 |
| 2026-05-26 | sop | `9edc9c6` — `WxOpsSourceWatcher`（反向 pull 模式起步） |
| 2026-05-31 | sop | `7ff8c94` — `message_inbox` 表落地 |
| 2026-06-01 | sop | `f52ca79` — 改包名 `ops_hub → sop_hub` |
| 2026-06-01 | wx | `f42d394` — wx 端跟改 import 字符串 |

**关键发现**：解耦的路径在 5-26 已经被你（或别的 agent）想清楚了，但**没有把 wx-ops-agent 那边的旧 push 模式拆掉**，结果两套架构并存了 6 天 +。

---

## 4. 当前调用流程图（文字版）

### 4.1 daemon 启动链

```
wx-ops-agent: $ python -m wechat_ops_agent.cli.main run-daemon ...
  └─ main.py line 489-509
     └─ _ensure_sop_hub_importable()    # 第 1 次 sys.path 注入
        └─ load_settings() ← sop-data-hub
           ↓
           daemon_interval / message_limit / tracking_rules_path 回填
  └─ start_daemon(...) → WechatDaemon.__init__
     └─ TrackerEngine(rules_path) → _load_rules()
        └─ _ensure_sop_hub_importable()  # 第 2 次（同进程，已 cached）
           └─ load_settings() ← sop-data-hub
              ↓
              listener_objects = hub_settings.monitored_groups   ← 群清单
              sop_route_contexts = ...                             ← 路由规则
  └─ daemon.run() 进入 while True
```

### 4.2 单条消息处理链（每 ~60s 的 poll）

```
WechatDaemon._run_monitor_cycle(db_dir)
  └─ _poll_group_messages(db_dir, group)
     ├─ query_messages_after_time(...) → rows: list[dict]
     ├─ process_message_rows(group_name, wxid, rows, context)
     │   for event in normalized_events:
     │     ├─ resolve_media_paths(db_dir, ..., message=event, month=...)   ← 关键入口
     │     │   ├─ if message_type == "image":
     │     │   │   ├─ decode_dat_to_jpg(...)                                    # wx 自己干
     │     │   │   ├─ shutil.copy2(decoded, ~/Documents/.../wechat_images/.../) # wx 自己干，标准落盘
     │     │   │   └─ _classify_and_archive_image(target_path)
     │     │   │       └─ process_image_via_sop_data_hub(...)               ★ 跨仓调用
     │     │   │           ├─ _ensure_sop_hub_importable()
     │     │   │           ├─ load_settings()
     │     │   │           ├─ settings.wechat_images_dir = ...              ← 改对方的运行时
     │     │   │           ├─ settings.extraction_output_dir = ...
     │     │   │           └─ process_new_image(resolved_image, settings)   # sop-data-hub
     │     │   │               ├─ classifier.classify(img)
     │     │   │               ├─ _run_extraction(...)
     │     │   │               ├─ _attach_reconcile_plan_if_possible(...)
     │     │   │               ├─ _move_processed_artifacts(...)             # 改文件位置
     │     │   │               ├─ _write_status_file(...)
     │     │   │               └─ _write_audit_record(...)                   # 写 audit DB
     │     │   │       ← 返回 ProcessingResult
     │     │   │   └─ shutil.move(...) to {group}/{category}/                # wx 又移动一次
     │     │   ├─ if message_type == "text":
     │     │   │   └─ _classify_and_route_text(text)
     │     │   │       └─ process_text_via_sop_data_hub(text, ...)         ★ 跨仓调用
     │     │   │           └─ BusinessDataAgent().ingest_business_text(text) # sop-data-hub
     │     │   │       ← 返回 {"status", "record_count", "record_ids"}
     │     │   └─ (video/file: 只查路径，不跨仓)
     │     └─ chat_record_store.append_records(...)                          # 追加到 jsonl
```

**核心问题肉眼可见**：
1. **同进程 + 同步阻塞**：classifier 跑 VLM、抽取跑 VLM，慢的话 daemon poll 节奏被拖死。
2. **文件被两方都搬一次**：wx 落到 `wechat_images/<group>/<月>/`，sop-data-hub `_move_processed_artifacts` 又搬一次，wx 拿到结果再 `shutil.move` 到 `{group}/{category}/`。三次搬运。
3. **配置被运行时覆写**：`settings.wechat_images_dir = ...` 这种调用方改被调方运行时状态的设计很脆弱。
4. **状态分裂**：jsonl 在 wx 这边、`message_inbox` / audit 在 sop 这边，没有统一 schema 在牵着。

---

## 5. 解耦方案

### 方案 A：HTTP/IPC 接口化（保留 push 模式）

**描述**：把 `sop_hub.runner.process_new_image` 和 `BusinessDataAgent.ingest_business_text` 包成 HTTP service（FastAPI 或 socket）。wx 这边的 `integrations/sop_data_hub.py` 改成 `requests.post(...)`。

**wx 端要改**：
- `integrations/sop_data_hub.py` 改为 HTTP client；`sys.path` 注入彻底删
- `cli/main.py` 启动时拿配置改成 HTTP `GET /config`
- `tracker.py` `_load_rules` 改 HTTP `GET /monitored-groups`
- `storage/paths.py` 删掉跨仓 import，落地路径走环境变量或本地 yaml

**sop 端要新增**：
- 一个新模块 `sop_hub/api/server.py`，至少暴露 4 个端点：
  - `POST /image` → 包装 `process_new_image`
  - `POST /text` → 包装 `BusinessDataAgent.ingest_business_text`
  - `GET /config` → 返回 `Settings` 子集
  - `GET /monitored-groups` → 返回 `tracking_tasks` + `monitored_groups`
- 新一个 `python -m sop_hub serve` CLI 子命令
- 多进程 / 单进程的部署文档

**优点**：
- 拓扑直观：wx 还是触发方，但只通过网络协议
- 易测试（HTTP 比 sys.path 容易 mock）
- 容易扩展到多机部署

**缺点 / 风险**：
- 仍然是 push 模式 + 同步阻塞，慢的图片抽取会拖死 daemon 的 poll 节奏
- 多了一层 HTTP，但语义跟"同进程函数调用"几乎一样，**没解决根本架构问题**
- 需要管两个进程的生命周期（systemd / launchd / pm2）
- 需要保留双向链路，文件路径仍要协调（wx 写到哪、sop 从哪读）

### 方案 B：Watcher 拉模式 + wx-ops-agent 只落盘（**推荐**）

**描述**：彻底反转方向。wx-ops-agent 退化为"微信侧爬虫 + 落盘工具"，把每条消息（含解码后的图片路径）写到 `data/chat_records/<group>/<month>.jsonl` 就 done。sop-data-hub 自己跑一个独立 daemon，复用已经存在的 `WxOpsSourceWatcher.iter_message_events()`，自己消费 jsonl，自己做分类/抽取/落库。

**wx 端要改**：
1. **删** `src/wechat_ops_agent/integrations/sop_data_hub.py` 整个文件
2. **删** `src/wechat_ops_agent/chat_records/media_resolver.py` 第 11、116、178 行的跨仓调用（`_classify_and_archive_image` + `_classify_and_route_text` 整体删除或退化为纯落盘）
3. **删** `src/wechat_ops_agent/tracking/handlers/business_image_router.py`（注释自己说了"compatibility retained"，可以删；保留的话也要把 251 行那次 hub 调用拆掉）
4. **删** `src/wechat_ops_agent/ocr/business_group/*`（跟 sop-data-hub 重复，是分类器双轨的根源）
5. **改** `src/wechat_ops_agent/tracking/tracker.py` 第 66-78 行：群清单改成读 `data/runtime/monitored_groups.json`，由 sop-data-hub 周期写文件
6. **改** `src/wechat_ops_agent/cli/main.py` 第 489-509 行：参数 fallback 改环境变量
7. **改** `src/wechat_ops_agent/storage/paths.py`：删除跨仓 import 路径，只保留环境变量

**sop 端要新增**：
1. 一个 daemon 入口：`python -m sop_hub watch` （新 CLI 子命令）
2. 主循环：
   ```python
   watcher = WxOpsSourceWatcher()
   while True:
       for event in watcher.iter_message_events(since=cursor):
           if event.message_type == "image" and event.raw_asset_bundle.raw_image_path:
               process_new_image(event.raw_asset_bundle.raw_image_path, settings,
                                 month_str=..., group_name=event.metadata["group_name"])
           elif event.message_type == "text" and event.text:
               BusinessDataAgent().ingest_business_text(event.text)
           upsert_message_inbox_event(event)  # 已有
           cursor = event.message_id
       sleep(2)
   ```
3. 周期性把 `monitored_groups`/`tracking_tasks` 写到 `wx-ops-agent/data/runtime/monitored_groups.json`（替代当前 wx 主动 pull）
4. 文档化两边的边界

**优点**：
- **方向对**：sop-data-hub 是消费者，自己掌握节奏，可以异步、批量、并行
- **零跨仓 import**：wx 完全不知道 sop 的存在，连环境变量都只需要约定路径
- **复用现成代码**：`WxOpsSourceWatcher` + `message_inbox` 已就绪，不是新 infra
- **故障隔离**：sop 挂了 wx 还能继续落盘；wx 挂了 sop 处理完队头就停
- **天然测试**：sop 端可以离线重放 jsonl
- **重启友好**：cursor 持久化即可

**缺点 / 风险**：
- 工作量比方案 A 大：要删 4-5 个 wx 端文件 + 写 sop 端的 daemon CLI + cursor 管理
- 文件契约要明确：`chat_records/<group>/<month>.jsonl` 的 schema 必须冻结（看 `message_inbox.message_event_to_inbox_row` 现在用什么字段）
- 中唐特钢线现网在跑，迁移要分两步走（先文本，后图片）
- 实时性：watcher 默认 sleep 2-5s，对"出港计划通知单收到秒回"场景延迟有几秒可见

### 方案 C：渐进折中（用方案 B 的目标 + 方案 A 的过渡）

**描述**：先按方案 A 做 HTTP 化解决"sys.path 注入"的代码异味（小工作量），跑 1-2 周稳定后再按方案 B 做架构反转（大工作量）。

**优点**：风险可控、可分批 ship  
**缺点**：会临时再增加一层 HTTP 代码，最后还要再删

---

## 6. 推荐方案

**推荐方案 B**。

**一句话理由**：sop-data-hub 在 5-26 已经把"反向 pull"的基础设施（`WxOpsSourceWatcher` + `message_inbox`）建好了，今天再做方案 A 等于绕过这个现成轮子重新发明同步 HTTP；正确的事是把 wx 端那 4 个跨仓 import 调用点全部干掉，让 sop-data-hub 接管自己的消费节奏。

---

## 7. 关键附注

### 7.1 wx-ops-agent 除了 sop_hub 调用之外干了哪些活

读 `src/wechat_ops_agent/` 目录，**真正属于"微信侧"的职责**有：

- `chat_records/`：从加密 DB 拉消息、媒体路径解析、ledger 落地（**应保留**）
- `cli/daemon.py`：UI bridge 触发 + DB 轮询 + jsonl 写入（**应保留**）
- `db/`：sqlcipher 解密、消息表名计算（**应保留**）
- `key_manager/`：从 macOS keychain 拿微信密钥（**应保留**）
- `tracking/image/`：dat 文件解码 / hardlink 查找（**应保留**，这是微信特有的）
- `models/group_member_map.py`：群成员名映射（**应保留**）
- `sentinel/`：图片同步 todo 管理（**应保留**，跟微信文件系统状态强相关）
- `utils/wechat_index.py`：联系人索引（**应保留**）
- `ocr/business_group/`：业务图片分类 + OCR pipeline（**应删，跟 sop-data-hub 重复**）
- `integrations/sop_data_hub.py`：跨仓桥（**应删**）
- `tracking/handlers/business_image_router.py`：旧业务分发（**应删**）
- `chat_records/media_resolver.py` 里的 `_classify_and_archive_image` / `_classify_and_route_text`（**应删**）

也就是说：wx 端有相当于 ~6 个核心子系统应该留下，3-4 个是历史耦合点应该砍。

### 7.2 路径协调

当前两边怎么知道对方的路径：
- wx → sop：`get_sop_data_hub_root()` 默认 `/Users/qicai21/projects/repos/sop-data-hub`，可被环境变量 `WX_OPS_AGENT_SOP_DATA_HUB_ROOT` / `WX_OPS_AGENT_SOP_DATA_HUB_SRC` 覆盖
- sop → wx：`_default_wx_ops_agent_root()` 用 `__file__.parents[4] / "wx-ops-agent"`（相对路径推断），可被 `WX_OPS_AGENT_ROOT` / `WX_OPS_AGENT_CHAT_RECORDS_DIR` 覆盖
- 共享落地：`~/Documents/bussiness-artifacts/wechat_images`（默认）+ `WX_OPS_AGENT_WECHAT_IMAGE_ROOT`

**对方案 B 的影响**：sop 端的路径推断已经完备（5-26 commit 写好的），不需要 wx 反过来告诉它；wx 端只要保留"图片落到 `WX_OPS_AGENT_WECHAT_IMAGE_ROOT`、jsonl 落到 `data/chat_records/`"两条约定即可，根本不需要 import 对方。

### 7.3 测试残留

`tests/unit/test_ops_data_hub_bridge.py`、`tests/unit/test_media_resolver.py` 仍然用 `process_image_via_ops_data_hub` 旧名字（没被 `f42d394` 改），如果做方案 B 这两个测试文件会随着模块删除一起删掉，不是大问题。

### 7.4 双轨分类器的痛

`wx-ops-agent/src/wechat_ops_agent/ocr/business_group/classifier.py` 和 `sop-data-hub/src/sop_hub/classifier/classifier.py` 两边都叫 `BusinessGroupImageClassifier`，做的事一模一样（VLM 调用 + 类目判定）。这是 4-23 commit 留下的双胞胎，方案 B 实施时是删 wx 那份。如果方案 A 强行保留，这个双轨会继续存在。

### 7.5 1 个奇怪的细节

`wx-ops-agent/src/wechat_ops_agent/storage/paths.py` line 17：

```python
def get_wechat_image_root() -> Path:
    raw = os.environ.get("WX_OPS_AGENT_WECHAT_IMAGE_ROOT")
    if raw:
        return Path(raw).expanduser()
    try:
        from wechat_ops_agent.storage.paths import get_sop_data_hub_src  # ← 自模块 import
        import sys
        ...
```

这个 `from wechat_ops_agent.storage.paths import get_sop_data_hub_src` 是从同一个文件里 import 同文件的函数，完全没必要，应该是历史改动时遗留的。方案 B 实施时这行可以一起删。

---

## 8. 落地步骤建议（如果走方案 B）

> 仅供后续执行参考，不在本笔记任务范围。

**Phase 1（轻量、可独立交付）**：剥离文本路由
1. 在 sop-data-hub 加 `python -m sop_hub watch-text` 子命令，循环跑 `WxOpsSourceWatcher.iter_message_events(message_type="text")` → `BusinessDataAgent.ingest_business_text`
2. 删 `wx-ops-agent/src/wechat_ops_agent/chat_records/media_resolver.py` 第 178 行的 `_classify_and_route_text(text)` 调用
3. 删 `process_text_via_sop_data_hub` 函数
4. 跑 1-2 天看 jsonl → DB 是不是顺畅

**Phase 2（重头戏）**：剥离图片处理
1. 在 sop-data-hub 加 `python -m sop_hub watch-image` 子命令，循环跑 `iter_message_events(message_type="image")` → `process_new_image`
2. 让 sop 这边自己 `_move_processed_artifacts` 到 `{group}/{category}/`（已经在做了）
3. 删 wx 端的 `_classify_and_archive_image`、`business_image_router`、`integrations/sop_data_hub.py`、`ocr/business_group/*`
4. 跑回归（中唐特钢出港计划链路 + 朝阳钢铁检装车链路）

**Phase 3**：清理配置共享
1. sop-data-hub 周期写 `wx-ops-agent/data/runtime/monitored_groups.json`
2. wx 端 `TrackerEngine._load_rules` 改读文件
3. wx 端 `cli/main.py run-daemon` 的参数 fallback 改环境变量
4. 最后删 `_ensure_sop_hub_importable` 和 `storage/paths.py` 里的跨仓 import 逻辑
