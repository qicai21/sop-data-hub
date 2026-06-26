# sop-data-hub

**锦州港铁路+海铁联运业务系统**:微信群消息流 → 业务实体抽取 + 95306 票务交叉 + 自动出港 excel 回群。

多项目并存,SOP yaml 驱动。一切始于 wx-ops-agent 落到磁盘的 jsonl + 图片,终于 wx-ui-bridge 把 excel 发回群。

> **新 session / 接手 / 隔几月回来**:先读 [`docs/00-START-HERE.md`](docs/00-START-HERE.md) — 5 分钟摸清当前架构 + 5 个 daemon 怎么起 + 业务铁律。

---

## 1. 当前业务面

| 项目 | 状态 |
|---|---|
| 吉林金钢(锦州) | 主项目,完整 release_batch + 发运 chain 闭环 |
| 朝阳钢铁 | 检装车通知单 + 95306 chain(2026-06-02 实测打通) |
| 中唐特钢 | 历史 batch 完整,在用 |
| 九三循环运输 | 占位预留(数据待完善) |
| 乌兰浩特特钢 | 自动发现(有 1 条 batch) |
| (规划)铜精矿循环 / 氧化铝循环 / 其他循环运输 | — |

---

## 2. 5 个常驻进程

```
微信群 → wx-ops-agent run-daemon ──落 jsonl + 解码 jpg到磁盘
                                      ↓
              ┌─── run_live_service.py ───扫 jsonl
              │
              ├─[文本]→ message_inbox + text_router 分类
              │            ↓
              │   text_watch_daemon → BusinessDataAgent.ingest_business_text
              │            ↓
              │      release_batches 入业务库
              │
              └─[图片]→ process_new_image
                           ↓
                     VLM 分类 + 抽取(yaml 驱动)
                           ↓
                  inspection_ingestion_candidates(三层级联推断:B 文本融合 / A 证据打分 / 都不通挂起)
                           ↓
                  chain → match release_batch → 95306 查 → wagon_shipments → excel → 发回群

               rail95306-sync run_sync_worker.py ── 5min/轮 同步 95306 票
                              ↓ 每轮完调用
                       pending_match_verifier ── 票延迟 1-2h 候选自动重试
                                            ── 6h 超时 → 人工
```

**检查健康态:**
```bash
ps aux | grep -E "wechat_ops_agent|run_live_service|text_watch_daemon|run_sync_worker" | grep -v grep
```

应有 4 个进程(看板 cli_dashboard 是手开 tmux,不计)。

---

## 3. 技术栈

| 组件 | 选型 |
|---|---|
| Python | 3.14 (`/opt/homebrew/bin/python3.14`) |
| 数据持久化 | SQLite(WAL 模式) — 业务库 `data/sop_agent.db`,只读引用 rail95306-sync 的 `~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3` |
| VLM(分类+抽取) | Qwen3.6-35B-A3B-4bit @ `localhost:8021`(OpenAI `/v1`,单模型兼两职;2026-06-26 起取代旧 VL-8B@8018 / 14B@8020) |
| VLM 推理后端 | Apple MLX(mlx-vlm.server 0.6.3) |
| 微信侧 | wx-ui-bridge(自动化 + 文件发送) |
| 看板 | 终端(stdlib ANSI,无第三方依赖) |

**关联仓库:**
- `~/projects/repos/wx-ops-agent` — 微信消息采集 + 图片解码(单一职责)
- `~/projects/repos/rail95306-sync` — 95306 票数据同步(**数据不可重建**)
- `~/projects/repos/sop-data-hub`(本仓)— 业务系统中心

---

## 4. 快速开始

```bash
cd ~/projects/repos/sop-data-hub

# 看当前业务态(终端看板)
PYTHONPATH=src python3 scripts/cli_dashboard.py
# 或挂 tmux 后台:
tmux new -s board -d "cd $(pwd) && PYTHONPATH=src python3 scripts/cli_dashboard.py"

# 看候选挂起情况
PYTHONPATH=src python3 -m sop_hub.sop.pending_match_verifier --db data/sop_agent.db --once

# 手动重算 wx 配置(改了 config/project_sops/*.yaml 后)
PYTHONPATH=src python3 -m sop_hub.sop.wx_config_exporter

# 启动 text_watch_daemon(若不在跑)
nohup env PYTHONPATH=src /opt/homebrew/bin/python3.14 \
  -m sop_hub.sop.text_watch_daemon --db data/sop_agent.db \
  --interval 5 --quiet >> runtime/text_watch.log 2>&1 & disown

# sqlite 直查(无 cache,看实时态)
sqlite3 data/sop_agent.db \
  "SELECT project, ship_name, batch_sequence, dispatch_status FROM release_batches WHERE dispatch_status='in_progress'"
```

CLI 子命令清单见 `python3 -m sop_hub --help`(主要:`process` / `inspect` / `departure` / `ingest` / `list-batches` / `pending drop`)。

---

## 5. 业务铁律

详见 [`docs/00-START-HERE.md` §4](docs/00-START-HERE.md#4-业务铁律memory-备份先读这个)。摘要:

1. **rail95306-sync 数据不可重建** — 其他全部都可以
2. **时间戳:ISO Beijing +08:00**(`sop_hub.utils.time.now_iso_beijing()`),绝不 `datetime.utcnow()` / 裸 `datetime.now()` / `CURRENT_TIMESTAMP`
3. **wx-ops-agent → sop-data-hub 单向数据流**:wx 只产 jsonl + 图片,sop 自己消费
4. **检装车候选无船名 → 必挂起**,不允许往下游传脏数据
5. **95306 票延迟 1-2 h**:候选 `pending_95306_match` 正常,6h 超时再报警
6. **多项目通过 yaml 驱动**(`config/project_sops/*.yaml`)

---

## 6. 文档地图

```
docs/
├── 00-START-HERE.md           ← 新 session 5 分钟版
├── PITFALLS.md                ← 历史踩坑(必读)
├── business-rules/            ← 业务铁律
│   ├── 标准名称.md             ← 业务文档/分类的标准命名
│   ├── 出港计划通知单_rolls.md  ← 放货单识别规则
│   └── 收发货单位和车站匹配关系.md
└── project-sops/              ← 各项目 SOP 业务文档(人类版;机器版在 config/project_sops/*.yaml)

config/project_sops/*.yaml     ← 项目 SOP 机器版(代码读取)
notes/<日期>/                   ← 时序笔记(调查/重构)
deprecated/                    ← 退役内容
└── docs_pre_20260601/         ← 5/31 之前的架构/工单(可疑)
```

---

## 7. 代码地图

```
src/sop_hub/
├── sop/                       ← 业务系统主体(daemon + chain + 工具)
├── data_agent/                ← BusinessDataAgent + DB schema
├── engines/                   ← VLM 引擎(inspection / departure / handwritten / materials)
├── classifier/                ← 图片分类
├── calc/                      ← yaml DSL 计算(R76 shipped_weight 那套)
├── matching/                  ← inspection vs 95306 比对
├── pipeline/                  ← 旧 pipeline 入口
├── utils/                     ← time 等 helper
└── cli.py                     ← 主 CLI

scripts/
├── cli_dashboard.py           ← 终端看板(2026-06-02 替代 r74_dashboard_server)
├── run_live_service.py        ← 主 daemon
└── business_query.py          ← 运维 CLI(reconcile / set-dispatch / assign 等)

config/project_sops/           ← 项目 SOP yaml
data/sop_agent.db              ← 业务 SQLite
runtime/                       ← daemon 运行时(cursor / log / event)
```

---

## 8. 历史回顾(2026-06-02 重构)

一天 12+ commits 完成:
- **Phase 1-4**:wx-ops-agent ↔ sop-data-hub 解耦(跨仓 import 6 → 0)+ wx OCR 子系统下线(-3839 行)
- **CLI 看板**:替换 r74 HTML+JSON 老栈(修了多月"只显示吉林金钢"bug 的根因 = SQL 漏过滤项目)
- **时区统一**:全仓 timestamp 写入端 100% Beijing ISO `+08:00`,verifier elapsed 不再偏 8h
- **dead code 清理**:dispatch_board.py 整文件删(-2859 行)
- **infer_candidate_context**:三层级联(B 文本融合 → A 证据打分 → 强挂起),解决"船名空就跑链"事故

详细变更见 [`notes/2026-06-01-night-survey/`](notes/2026-06-01-night-survey/)(A1/A2 调查)和 git log。

---

## 9. License / Author

私有项目(郭东北自用)。
