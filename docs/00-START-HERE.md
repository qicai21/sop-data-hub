# sop-data-hub — 新 session 5 分钟摸清

> 这一篇专门给"刚开 session 的 Claude / 接手的新人 / 几个月没碰回来的我自己"看。
> 读完应该能立刻干活,不必从 git log 倒查。

最后更新:2026-06-09(daemon 全部迁 launchd 后)

---

## 1. 这是干啥的

**锦州港铁路+海铁联运业务系统**(郭东北自用)。

- 微信群消息(图片 / 文本) → 业务实体(release_batch / wagon_shipment / 出港 excel) → 自动回群
- 多项目并存:吉林金钢(锦州)、朝阳钢铁、中唐特钢、九三(占位)等
- 95306(中铁电子货运)数据交叉印证发运车号

---

## 2. 4 个常驻 daemon + 1 个手开看板

任何时候健康的系统大概是这样(**2026-06-09 起全部 launchd 管理**,Mac 重启自动恢复):

| launchd Label | 角色 | cwd |
|---|---|---|
| `com.qicai21.wechat-ops-agent` | 拉微信 + 解码图片 + 落 jsonl(**只产数据,不做业务**) | `~/projects/repos/wx-ops-agent` |
| `com.qicai21.sop-data-hub.live-service` | 扫 jsonl → `message_inbox` + VLM 分类 + `process_new_image` | `~/projects/repos/sop-data-hub` |
| `com.qicai21.sop-data-hub.text-watch` | inbox 文本 → ingest + 跑 chain(含 `--run-chains`) | 同上 |
| `com.qicai21.rail95306-sync` | 5 min/轮同步 95306 + 调 `pending_match_verifier` | `~/projects/repos/rail95306-sync` |
| `scripts/cli_dashboard.py`(tmux 手开) | 终端看板(直查 sqlite,5s 刷新) | sop-data-hub |

```bash
launchctl list | grep -E "com.qicai" | sort           # launchd 视角
ps aux | grep -E "wechat_ops_agent|run_live_service|text_watch_daemon|run_sync_worker" | grep -v grep   # 进程视角(4 个)
```

plist 在 `~/Library/LaunchAgents/`,都有 `RunAtLoad=true + KeepAlive=true`,Mac 重启 / daemon 崩溃都会自动拉起。每个 plist 显式设了 `PATH=/opt/homebrew/bin:/usr/bin:/bin`(否则 wx-ops-agent 找不到 `sqlcipher`)、text-watch 显式 `--run-chains`(否则外部链永远 pending)。

---

## 3. 怎么重启每个 daemon

**改了代码或卡死时 → `launchctl kickstart -k`**(launchd 自动起新实例):

```bash
launchctl kickstart -k "gui/$(id -u)/com.qicai21.wechat-ops-agent"
launchctl kickstart -k "gui/$(id -u)/com.qicai21.sop-data-hub.live-service"
launchctl kickstart -k "gui/$(id -u)/com.qicai21.sop-data-hub.text-watch"
launchctl kickstart -k "gui/$(id -u)/com.qicai21.rail95306-sync"
launchctl kickstart -k "gui/$(id -u)/com.qicai.qwen3vl.mlx"      # 顺手:VLM 服务
```

改代码触发哪个重启:

- 改 `agent.py` / `runner.py` / image route → kickstart **live-service** + **text-watch**(内存里是老 module 不会 hot reload)
- 改 chain / executor / freight_detail / lifecycle / engines/* → kickstart **text-watch**(live-service 不跑 chain 不用动)
- 改 wx UI 行为 / zstd → kickstart **wechat-ops-agent**
- 改 95306 query / verifier → kickstart **rail95306-sync**

**⚠️ 千万别再用老的 `nohup … & disown`**(2026-06-08 重启之前的方式)。那种 daemon PPID=1 但脱离 launchd 管理,Mac 重启就死,**不会自动恢复**。2026-06-08 中唐 36 车 9 小时静默就栽在这里。

```bash
# CLI 看板(不是 daemon,手开)
tmux new -s board -d "cd ~/projects/repos/sop-data-hub && \
  PYTHONPATH=src python3 scripts/cli_dashboard.py"
tmux attach -t board   # Ctrl-B D 后台,Ctrl-C 退出
```

---

## 4. 业务铁律(memory 备份,先读这个)

1. **rail95306-sync 数据不可重建** — 其他全部都可以(用户原话)
2. **时间戳:ISO Beijing +08:00** — 用 `sop_hub.utils.time.now_iso_beijing()`,**绝不用** `datetime.utcnow()` / 裸 `datetime.now()` / `CURRENT_TIMESTAMP`
3. **wx-ops-agent → sop-data-hub 单向数据流** — wx 只产 jsonl + 解码图片;sop 自己扫(不要再做 push 模式)
4. **检装车 candidate 必须先有 release_batch 才能匹配** — 没船名硬挂起 `pending_review`,不然今早那种 stale excel 事故会再来
5. **95306 票延迟 1-2 h** — 候选 `pending_95306_match` 状态正常,6h 超时才报警
6. **多项目通过 yaml 驱动**(理想态)— 详见 [notes/2026-06-01-night-survey/01-yaml-sop-coverage.md](../notes/2026-06-01-night-survey/01-yaml-sop-coverage.md),目前 yaml 驱动度约 24%,还有许多硬编码 gap

---

## 5. 看板 + 诊断

| 想看什么 | 怎么看 |
|---|---|
| 各项目 batch 进度 + 系统态 | `tmux attach -t board`(或 `python3 scripts/cli_dashboard.py`) |
| 挂起的检装车候选 | `PYTHONPATH=src python3 -m sop_hub.sop.pending_match_verifier --db data/sop_agent.db --once` |
| 哪些 inbox 文本还没 ingest | `sqlite3 data/sop_agent.db "SELECT COUNT(*) FROM message_inbox WHERE msg_type='text' AND (db_action IS NULL OR db_action NOT LIKE 'text_ingest_%')"` |
| 95306 最新票时间 | `sqlite3 ~/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3 "SELECT MAX(ticketed_at) FROM shipments"` |
| SOP yaml 改了后让 wx daemon 知道 | `PYTHONPATH=src python3 -m sop_hub.sop.wx_config_exporter`,然后重启 wx daemon |

---

## 6. 文档地图

```
docs/
├── 00-START-HERE.md                       ← 本文件
├── PITFALLS.md                            ← 历史踩坑(读一遍能省 1 周血泪)
├── business-rules/                        ← 业务铁律
│   ├── 标准名称.md                          ← 业务文档/分类标准命名
│   ├── 出港计划通知单_rolls.md               ← 放货单识别规则
│   └── 收发货单位和车站匹配关系.md           ← 单位/车站昵称字典
└── project-sops/                          ← 各项目 SOP 业务文档(人类版,机器版在 config/project_sops/*.yaml)
    ├── chaoyang_steel.md
    ├── jilin_jingang_jinzhou.md
    ├── jiusan.md
    └── zhongtang_special_steel.md

config/project_sops/*.yaml                 ← 项目 SOP 机器读的真源
notes/<日期>/                               ← 时序笔记(调查/重构纪要)
deprecated/                                ← 已退役 — 不要再依赖
└── docs_pre_20260601/                     ← 5/31 cutoff 之前的架构/工单
```

---

## 7. 代码地图(只列关键)

```
src/sop_hub/
├── sop/                                  ← 业务系统主体
│   ├── source_watcher.py                 ← WxOpsSourceWatcher(扫 wx jsonl)
│   ├── message_inbox.py                  ← 统一消息收件箱(DB 表 + DAO)
│   ├── text_router.py                    ← 文本 → SOP 分类 + 入 inbox
│   ├── text_watch_daemon.py              ← inbox text → ingest_business_text
│   ├── match_release_batch.py            ← ship/dest/cargo 匹配 release_batch
│   ├── infer_candidate_context.py        ← 三层级联推断候选 ship/dest/cargo
│   ├── workflow_task_executor.py         ← chain 执行(_execute_chaoyang_inspection_chain 等)
│   ├── pending_match_verifier.py         ← 候选挂起 → 6h timeout 验证器
│   ├── shipped_weight.py                 ← yaml DSL 驱动的发运重量计算
│   ├── departure_excel.py                ← 出港 excel 生成
│   ├── send_excel.py                     ← 调 wx-ui-bridge 发回群
│   └── wx_config_exporter.py             ← 给 wx daemon 写 wx_config.json
├── data_agent/
│   ├── agent.py                          ← BusinessDataAgent(release_batch / inspection ingest)
│   └── db.py                             ← sqlite 连接 + 表 schema
├── utils/
│   └── time.py                           ← ★ 时间戳唯一入口(now_iso_beijing / parse_any_timestamp)
├── engines/                              ← VLM 引擎(inspection / departure / handwritten)
└── cli.py                                ← 命令行入口(常用:ingest / list-batches / pending drop)

scripts/
├── run_live_service.py                   ← 主 daemon
├── cli_dashboard.py                      ← 看板
└── business_query.py                     ← reconcile / set-dispatch-status / assign 等运维 CLI

config/project_sops/                      ← 项目 SOP yaml(机器版)
```

---

## 8. 想从头摸的话(读这个顺序)

1. 本文件
2. [PITFALLS.md](PITFALLS.md)
3. [business-rules/](business-rules/) 三篇业务铁律
4. [project-sops/](project-sops/) 任挑一个项目读它的 md(看业务长啥样)
5. `config/project_sops/<同项目>.yaml`(看机器版怎么写)
6. `src/sop_hub/sop/text_watch_daemon.py` + `infer_candidate_context.py` + `workflow_task_executor.py`(全链 ~1000 行,读完掌握核心业务流)

---

## 9. 不要做的事

- 不要新建跨仓 import(2026-06-02 解耦四部曲刚清光,详见 [notes/2026-06-01-night-survey/02-wx-ops-coupling.md](../notes/2026-06-01-night-survey/02-wx-ops-coupling.md))
- 不要在 `data_agent/agent.py` 里给 BusinessDataAgent 加项目硬编码(yaml 驱动)
- 不要给 `BusinessDataAgent.ingest_business_text` 加 SOP 过滤(它自己已经只认放货指令格式)
- 不要把候选(`inspection_ingestion_candidates`)的 `ship_name` 留空就跑 chain(必挂起)
- 不要直接动 sqlite 在 live_service 跑时(用 `kill -STOP <pid>; sql; kill -CONT <pid>`)
- 不要 `git push --force` / `git reset --hard` / `rm -rf` 任何 rail95306-sync 数据
