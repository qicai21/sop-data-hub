# Data Persistence Topology Audit

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Scope:** qicai21/prompts_and_reports, sop-data-hub, wx-ops-agent, rail95306-sync

---

## 1. Database Inventory

| DB File | Location | Type | Size | Owner |
|---------|----------|------|------|-------|
| `agent.db` | `wx-ops-agent/data/agent.db` | SQLite | 116 KB | wx-ops-agent |
| `agent.db` | `ops-data-hub/data/agent.db` | SQLite | 1.1 MB | ops-data-hub |
| `95306_collection.sqlite3` | `rail95306-sync/runtime/95306_collection.sqlite3` | SQLite | 75.7 GB | rail95306-sync |
| `jiusan_cycle.db` | `ops-data-hub/data/jiusan_cycle.db` | SQLite | 320 KB | 九三大豆 |

- **活跃DB**: `wx-ops-agent/agent.db`, `ops-data-hub/agent.db`, `95306_collection.sqlite3`, `jiusan_cycle.db` — 共 4 个
- **已清理僵尸DB**: `ops_data_hub.db`, `rail95306.db`, `message_store.db` — R21 已删除
- **备份**: `wx-ops-agent/data/` 下有 4 个 `.bak` 文件和 `cleanup_backups/` 目录（约 48GB）

---

## 2. DB Contents — Table-Level Breakdown

### wx-ops-agent / agent.db

| Table | Rows | Purpose |
|-------|------|---------|
| `release_batches` | 3 | 放货批次（调度级业务对象） |
| `release_dispatch_match_rules` | 3 | 放货→发运匹配规则 |
| `contracts` | 0 | 合同（空） |
| `image_ingestion_audit` | 27 | 图片摄入审计日志 |
| `inspection_ingestion_candidates` | 0 | 检装车候选（空） |

**分类**: 缓存/派生数据 — 从 WeChat 消息 + 95306 API 派生

### ops-data-hub / agent.db

| Table | Rows | Purpose |
|-------|------|---------|
| `release_batches` | 29 | 放货批次（ops-data-hub 副本，数据更多） |
| `release_dispatch_match_rules` | 17 | 放货→发运匹配规则 |
| `contracts` | 4 | 合同记录 |
| `wagon_shipments` | 170 | 单车发运记录 |
| `departure_records` | 6 | 发车记录 |
| `image_ingestion_audit` | 338 | 图片摄入审计 |
| `inspection_ingestion_candidates` | 21 | 检装车候选 |
| `report_tasks` | 5 | 报表任务 |

**分类**: 缓存/派生数据 — 从 wx-ops-agent 数据 + 95306 API 派生

### 95306_collection.sqlite3 (75.7 GB)

| Table | Rows | Purpose |
|-------|------|---------|
| `shipment_snapshots` | 5,595,671 | **事实源** — 每车每次查询的时间点快照 |
| `query_run_pages` | 254,940 | 查询结果分页记录 |
| `shipments` | 19,610 | 运单维度聚合视图 |
| `raw_api_responses` | 12,017 | **事实源** — 95306 API 原始 JSON 响应 |
| `query_runs` | 12,093 | 查询执行记录 |
| `shipment_release_batch_matches` | 694 | 95306运单↔放货批次关联 |
| `stations` | 19 | 车站字典 |
| `session_states` | 4 | 95306 登录会话状态 |

### jiusan_cycle.db (320 KB)

| Purpose | Schema | Location |
|---------|--------|----------|
| 九三大豆循环运输资源账系统 V3 | `schema/jiusan_cycle_schema.sql` + `jiusan_cycle_v3_migration.sql` | `ops-data-hub/data/jiusan_cycle.db` |

包含: `jiusan_cycle_trains`, `jiusan_resource_pool`, `jiusan_tracking_status` 等表

---

## 3. 原始微信消息保存位置

```
~/projects/repos/wx-ops-agent/data/chat_records/
├── 铁晟业务工作群/
│   ├── 2026-03.jsonl       (122 KB)
│   ├── 2026-04.jsonl       (514 KB)
│   ├── 2026-05.jsonl       (488 KB)
│   └── image_download_todos/
├── 数据单发群-GROUP013/
│   ├── 2026-05.jsonl       (15 KB)
│   └── image_download_todos/
├── 中唐特钢发运群/
│   ├── 2026-04.jsonl       (12 KB)
│   └── 2026-05.jsonl       (10 KB)
└── 龙虾测试群/
    └── 2026-05.jsonl       (23 KB)
```

- **格式**: JSONL，每行一条消息
- **字段**: `seq`, `sender`, `sender-wxid`, `time`, `msg-type`, `msg-content`, `msg-path`, `remark`
- **分类**: **事实源 (Source of Truth)** — 这是所有下游数据的根

---

## 4. 图片保存位置

```
~/Documents/bussiness-artifacts/wechat_images/
├── 铁晟业务工作群/        (2,011 files)
├── 数据单发群-GROUP013/   (102 files)
├── 中唐特钢发运群/        (41 files)
├── 龙虾测试群/            (155 files)
├── unknown/               (1,133 files)
├── unmatched/             (96 files)
├── projects/              (119 files)
├── extractions/           (27 files — OCR results)
├── 检装车通知单/          (22 files)
├── 出港计划通知单/         (2 files)
├── 手写箱号车号表/         (3 files)
└── ...                    (other categories)
```

- **格式**: JPG/PNG 原图
- **OCR结果**: `extractions/` 子目录下的 `*_result.json` 文件（车号列表等）
- **分类**: **事实源** — 原始图片 + OCR 原始结果

---

## 5. OCR 结果保存位置

```
~/Documents/bussiness-artifacts/wechat_images/extractions/
├── 检装车通知单/          (22 个 *_result.json)
└── 出港计划通知单/         (2 个 *_result.json)
```

- **格式**: JSON — 每文件包含 `rows_count`, `car_nos[]`, `last_car_no`
- **分类**: **衍生数据** — 从原始图片 OCR 派生，但不是最终结构化数据

---

## 6. Runtime 目录

### sop-data-hub/runtime/

```
~/projects/repos/sop-data-hub/runtime/
├── live_service.pid
├── live_service.log
├── events/
│   └── <group_name>/<file_stem>/<message_id>.json   (event snapshots)
├── dashboard_intents/
│   └── <message_id>.json       (dashboard payload queue output)
└── dashboard_state/
    └── <message_id>.json       (dashboard state preview)
```

- **分类**: **运行态 (Runtime)** — 全部可删除重建
- **不跟踪**: `.gitignore` 已排除 `runtime/`

### rail95306-sync/runtime/

```
~/projects/repos/rail95306-sync/runtime/
├── 95306_collection.sqlite3   (75.7 GB — 核心数据)
├── 95306_ticket_*.json        (登录凭证快照 × 5 个账户)
├── 95306_storage_state_*.json (Playwright 浏览器状态 × 5 个账户)
├── 95306_keepalive.log
├── 95306_sync_worker.log
├── sync_worker.log
├── 95306_accounts.json        (账户配置)
├── screenshots/               (5 items)
└── 95306_preflight_report.json
```

- **分类**: `95306_collection.sqlite3` = **事实源 + 缓存混合**；其余 = **运行态**

---

## 7. Dashboard 相关数据目录

```
~/projects/repos/sop-data-hub/dashboard/
├── dispatch_board.html              (看板 HTML)
├── dispatch_board_data.json         (看板数据 — 运行时生成，不跟踪)
├── dispatch_board_data.example.json (示例)
├── dispatch_board_schema.md         (Schema 文档)
├── jiusan_dashboard.html            (九三看板)
├── jiusan_dashboard_data.json       (九三看板数据 — 运行时生成，不跟踪)
└── jiusan_dashboard_data.example.json
```

- `dispatch_board_data.json` — `.gitignore` 已排除，运行时由 `run_live_service` 从 `dashboard_intents/` + `dashboard_state/` 生成
- `jiusan_dashboard_data.json` — `.gitignore` 已排除，由九三脚本生成

---

## 8. Reports 目录

| 位置 | 数量 | 内容 |
|------|------|------|
| `sop-data-hub/reports/` | 50 篇 | SOP Data Hub 审计/部署/开发报告（.md + .xlsx） |
| `ops-data-hub/reports/` | 另行存在 | 老路径（迁移前） |
| `prompts_and_reports/reports/` | 67 items | 项目综合报告 + 业务数据报告 |
| `prompts_and_reports/orders/` | 29 items | 任务指令/order 文档 |
| `wx-ops-agent/data/reports/` | 9 files | wx-ops-agent 运行时报表 |

- **分类**: `sop-data-hub/reports/` = 项目审计报告（事实记录）；`prompts_and_reports/` = 综合归档

---

## 9. 95306 数据库与缓存

| 路径 | 大小 | 行数 | 分类 |
|------|------|------|------|
| `rail95306-sync/runtime/95306_collection.sqlite3` | 75.7 GB | 5.6M snapshots | **事实源+缓存** |
| `wx-ops-agent/data/cleanup_backups/.../95306_collection.sqlite3` | 47.7 GB | (副本) | 备份 |

**95306_collection 三层结构**:

1. `raw_api_responses` — 95306 API 原始 JSON（**事实源**）
2. `shipment_snapshots` — 每次查询的时间点快照，从 raw 派生（**派生**）
3. `shipments` — 运单维度聚合（**派生**）
4. `shipment_release_batch_matches` — 95306运单↔放货批次关联（**派生关联**）

---

## 10. 九三大豆数据库

```
ops-data-hub/data/jiusan_cycle.db    (320 KB)
```

- **Schema**: `schema/jiusan_cycle_schema.sql` + `schema/jiusan_cycle_v3_migration.sql`
- **脚本**: `scripts/jiusan_*.py` (9 个脚本)
- **看板**: `dashboard/jiusan_dashboard.html` + `jiusan_dashboard_data.json`
- **数据来源**: 95306 API + 入场数据 → 循环运输资源账
- **分类**: **派生数据库** — 从 95306 raw data 派生的业务视图

---

## 11. 普通货运数据库

普通货运没有独立数据库。数据分布在：

| 位置 | 表 | 用途 |
|------|-----|------|
| `wx-ops-agent/agent.db` | `release_batches` | 放货批次（初始摄入） |
| `ops-data-hub/agent.db` | `release_batches` (29), `wagon_shipments` (170), `contracts` (4), `departure_records` (6) | 放货批次 + 发运 + 合同（加工后） |
| `95306_collection.sqlite3` | `shipment_release_batch_matches` (694) | 95306运单↔放货关联 |
| `sop-data-hub/runtime/` | `events/`, `dashboard_intents/`, `dashboard_state/` | SOP 事件流 + 看板意图 |

- **分类**: 混合 — DB 表为**派生数据**，runtime JSON 为**运行态**

---

## 12. Source of Truth vs Cache vs Runtime 分类

### 🏛 Source of Truth（事实源 — 不可重建，丢失即永久丢失）

| 路径 | 格式 | 说明 |
|------|------|------|
| `wx-ops-agent/data/chat_records/**/*.jsonl` | JSONL | 原始 WeChat 消息 — 所有业务的根 |
| `Documents/bussiness-artifacts/wechat_images/**/*.{jpg,png}` | 图片 | 原始 WeChat 图片 |
| `95306_collection.sqlite3` → `raw_api_responses` | SQLite/JSON | 95306 API 原始响应 |
| `ops-data-hub/data/contracts/**/*.{pdf,docx}` | PDF/DOCX | 合同原始文件 |
| `wx-ops-agent/data/address_book.json` | JSON | 联系人地址簿 |
| `wx-ops-agent/data/entity_mapping.json` | JSON | 群组/实体映射 |
| `wx-ops-agent/config/tracking_rules.yaml` | YAML | 监听规则配置 |
| `prompts_and_reports/orders/` | Markdown | 任务指令归档 |

### 📊 Cache / 派生数据（可从事实源重建）

| 路径 | 格式 | 重建方式 |
|------|------|----------|
| `wx-ops-agent/agent.db` | SQLite | 从 chat_records + 95306 API 重建 |
| `ops-data-hub/agent.db` | SQLite | 从 wx-ops-agent/agent.db + 95306 重建 |
| `jiusan_cycle.db` | SQLite | 从 95306 raw 数据 + 入场数据重建 |
| `95306_collection.sqlite3` → `shipments`, `shipment_snapshots` | SQLite | 从 `raw_api_responses` 重建 |
| `95306_collection.sqlite3` → `shipment_release_batch_matches` | SQLite | 从 raw + agent.db 关联重建 |
| `Documents/bussiness-artifacts/wechat_images/extractions/*.json` | JSON | 从原始图片 OCR 重建 |
| `wx-ops-agent/data/group_member_maps/*.json` | JSON | 从微信 API 重建 |

### 🔄 Runtime / 运行态（可随时删除重建）

| 路径 | 说明 |
|------|------|
| `sop-data-hub/runtime/*` | SOP live service 全部输出 — `.gitignore` 已排除 |
| `rail95306-sync/runtime/*.log` | 同步日志 |
| `rail95306-sync/runtime/*.json` (tickets, state) | 登录状态、凭证 — 过期后失效 |
| `wx-ops-agent/data/runtime-logs/` | daemon 日志 |
| `wx-ops-agent/data/daemon.pid` | 进程 PID |
| `dashboard/dispatch_board_data.json` | 运行时生成 — `.gitignore` 排除 |
| `dashboard/jiusan_dashboard_data.json` | 运行时生成 — `.gitignore` 排除 |
| `wx-ops-agent/data/last_seen_timers.json` | 最后轮询时间戳 |

---

## 13. 完整生命周期落盘链

```
                            🏛 事实源                          📊 派生                        🔄 运行态

  微信消息
      │
      ▼
  chat_records/*.jsonl ──────────────────────────────────────── 事实源 (wx-ops-agent/data/)
      │
      │  WxOpsSourceWatcher
      ▼
  MessageEvent ───────────────────────────────────────────────── 内存对象 (sop-data-hub)
      │
      │  _write_event_snapshot()
      ▼
  runtime/events/<group>/<stem>/<id>.json ─────────────────── 运行态 (sop-data-hub/runtime/)
      │
      │  match_message_event() → MonitoringMatch
      │
      ├──► Matcher 匹配 (文本 / 文件类型)
      │
      ▼
  WorkflowTaskQueue ─────────────────────────────────────────── 内存对象
      │
      │  build_dashboard_payload_queue()
      ▼
  runtime/dashboard_intents/<id>.json ──────────────────────── 运行态
      │
      │  build_dashboard_state_preview()
      ▼
  runtime/dashboard_state/<id>.json ────────────────────────── 运行态
      │
      │  dashboard 渲染
      ▼
  dashboard/dispatch_board.html ────────────────────────────── 运行时展示
  dashboard/dispatch_board_data.json ───────────────────────── 运行态（.gitignore 排除）


  原始图片
      │
      ▼
  wechat_images/<group>/*.jpg ──────────────────────────────── 事实源
      │
      │  OCR (datvision / local-vision)
      ▼
  wechat_images/extractions/<category>/*_result.json ───────── 派生数据 (OCR结果)
      │
      │  inspection_ingestion
      ▼
  agent.db → inspection_ingestion_candidates ───────────────── 派生数据


  95306 API
      │
      ▼
  95306_collection.sqlite3 → raw_api_responses ─────────────── 事实源 (95306 原始 JSON)
      │
      │  shipment_snapshots (时间点快照)
      ▼
  95306_collection.sqlite3 → shipments ─────────────────────── 派生 (运单聚合)
      │
      │  reconcile
      ▼
  95306_collection.sqlite3 → shipment_release_batch_matches ─ 派生关联
      │
      │  report generation
      ▼
  reports/*.xlsx / reports/*.md ────────────────────────────── 最终产出


  合同文件 (.pdf / .docx)
      │
      ▼
  data/contracts/<project>/*.{pdf,docx} ────────────────────── 事实源
      │
      │  contract classification
      ▼
  agent.db → contracts ─────────────────────────────────────── 派生数据 (结构化合同摘要)
```

---

## 14. 目录树总览

```
~/projects/repos/
│
├── wx-ops-agent/
│   ├── data/
│   │   ├── chat_records/           🏛 事实源 — 原始微信消息 JSONL
│   │   ├── agent.db                📊 派生 — 放货批次/审计/匹配规则
│   │   ├── group_member_maps/      📊 派生 — 群成员映射
│   │   ├── address_book.json       🏛 事实源 — 联系人
│   │   ├── entity_mapping.json     🏛 事实源 — 实体映射
│   │   ├── last_seen_timers.json   🔄 运行态
│   │   ├── runtime-logs/           🔄 运行态
│   │   └── reports/               📊 派生 — 运行时报表
│   └── config/
│       └── tracking_rules.yaml     🏛 事实源 — 监听规则
│
├── ops-data-hub/  (老 checkout)
│   └── data/
│       ├── agent.db                📊 派生 — 带 wagon_shipments + contracts
│       ├── jiusan_cycle.db         📊 派生 — 九三大豆
│   ├── contracts/              🏛 事实源 — 合同文件
│
├── sop-data-hub/  (canonical checkout)
│   ├── runtime/                    🔄 运行态 (.gitignore 排除)
│   │   ├── events/
│   │   ├── dashboard_intents/
│   │   └── dashboard_state/
│   ├── dashboard/                  🏛 事实源 (HTML/示例) + 🔄 运行态 (data.json)
│   ├── reports/                    📊 派生 — 审计/部署报告
│   └── config/settings.yaml        🏛 事实源 — 路径配置
│
├── rail95306-sync/
│   └── runtime/                    🏛+📊+🔄 混合
│       ├── 95306_collection.sqlite3  (75.7 GB)
│       ├── 95306_ticket_*.json     🔄 登录凭证
│       └── 95306_storage_state_*.json  🔄 浏览器状态
│
└── prompts_and_reports/
    ├── orders/                     🏛 事实源 — 任务指令
    ├── reports/                    📊 派生 — 综合报告
    └── sops/                       🏛 事实源 — SOP 文档
│
~/Documents/bussiness-artifacts/
└── wechat_images/                  🏛 事实源 — 原始图片 + OCR结果
    ├── 铁晟业务工作群/             (2,011 images)
    ├── 数据单发群-GROUP013/        (102 images)
    └── extractions/               📊 派生 — OCR 结果 JSON
```

---

## 15. 总结

| 类别 | 数量 | 总大小 |
|------|------|--------|
| **事实源 (Source of Truth)** | 8 个关键路径 | ~2 GB (不含图片) |
| **派生/缓存 (Cache)** | 4 个 SQLite + JSONL 目录 | ~80 GB (主要是 95306_collection) |
| **运行态 (Runtime)** | 5 个目录 | ~500 MB |
| **已清理僵尸 DB** | 3 个已删除 (R21) | 0 bytes |
