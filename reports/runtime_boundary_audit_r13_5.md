# Runtime Boundary Audit R13.5

| 字段 | 内容 |
|---|---|
| Repository | `qicai21/ops-data-hub` |
| Branch | `codex/sop-real-sop-topology-audit-20260525` |
| Commit | `9edc9c6` |
| 执行日期 | `2026-05-26` |
| 任务类型 | audit-only |
| 说明 | 不实现功能，不改 watcher，不动数据库 |

## 1. 结论

本轮审计只看边界，不改代码。

- `ops-data-hub` 仓库里**没有**顶层 `runtime/` 目录；当前运行时状态实际落在 `data/`、外部 `wx-ops-agent` 目录，以及外部图片资产目录。
- 真正的业务输入不是 repo 里的测试文件，而是 `wx-ops-agent/data/chat_records/**/*.jsonl`、`/Users/qicai21/Documents/bussiness-artifacts/wechat_images/**` 里的图片/提取物，以及 `data/contracts/*` 里的合同文档。
- 真正该忽略的是运行态数据库、WAL/SHM、日志、缓存、`__pycache__`、`.DS_Store`、以及派生 JSON / XLSX 预览文件。
- 当前工作区仍然是脏的：有 1 个已修改文件、若干无关未跟踪文件；本轮不纳入提交。

## 2. git status

### 2.1 当前分支

- branch: `codex/sop-real-sop-topology-audit-20260525`
- HEAD: `9edc9c6`

### 2.2 当前状态摘要

- modified:
  - `dashboard/dispatch_board.html`
- untracked（当前工作区可见）:
  - `config/report_templates/`
  - `reports/2026-05-22_hermes_jiusan_container_audit_check_report.md`
  - `reports/2026-05-22_hermes_jiusan_cycle_train_identity_v1_report.md`
  - `reports/jljg_factory_upload_preview_20260521_1605.json`
  - `reports/jljg_factory_upload_preview_20260521_1623.json`
  - `reports/jljg_factory_upload_preview_20260521_1625.json`
  - `reports/jljg_factory_upload_preview_20260521_1626.json`
  - `reports/jljg_factory_upload_preview_20260523_1945.json`
  - `reports/吉林金钢_发运数据_20260521_1605.xlsx`
  - `reports/吉林金钢_发运数据_20260521_1608.xlsx`
  - `reports/吉林金钢_发运数据_20260521_1612.xlsx`
  - `reports/吉林金钢_发运数据_20260521_1623.xlsx`
  - `reports/吉林金钢_发运数据_20260521_1625.xlsx`
  - `reports/吉林金钢_发运数据_20260521_1626.xlsx`
  - `reports/吉林金钢_发运数据_20260523_1942.xlsx`
  - `reports/吉林金钢_发运数据_20260523_1945.xlsx`
  - `scripts/jiusan_baseline_absorb.py`
  - `src/ops_hub/reports/`
  - `tests/test_reports.py`
- ignored（按 `.gitignore` / 现状）:
  - `data/`
  - `*.db`
  - `*.log`
  - `.pytest_cache/`
  - `.venv/`
  - `__pycache__/`
  - `dashboard/dispatch_board_data.json`
  - `dashboard/jiusan_dashboard_data.json`

## 3. `runtime/*`

### 3.1 盘点结果

- repo 顶层不存在 `runtime/` 目录。
- 运行态逻辑在代码里被映射到 `data/runtime-logs/daemon-auto.log`，但那是 `wx-ops-agent` 侧路径，不是本仓库顶层目录。

### 3.2 结论

- `runtime/*` 本轮应视为**应忽略的运行态边界**，不应纳入版本控制。
- 若未来真的在 repo 内生成 `runtime/`，应按运行产物处理，不应作为业务源码。

## 4. `data/*`

### 4.1 当前磁盘内容

`data/` 目录当前存在，内容包括：

- `data/agent.db`
- `data/jiusan_cycle.db`
- `data/jiusan_cycle.db-wal`
- `data/jiusan_cycle.db-shm`
- `data/legacy_jiusan_release_batches_backup.json`
- `data/95306_collection.sqlite3`
- `data/contracts/chaoyang_steel/物流服务委托合同.pdf`
- `data/contracts/jilin_jingang/SYSJDL-20260101A_沈阳盛京运输合同.docx`
- `data/contracts/jiusan_soybean/物流发展-铁盛2026大豆合同.docx`
- `.DS_Store`

### 4.2 分类

#### 应忽略

- 所有数据库 / 事务文件：
  - `*.db`
  - `*.db-wal`
  - `*.db-shm`
  - `data/agent.db`
  - `data/jiusan_cycle.db*`
- 运行垃圾：
  - `data/.DS_Store`

#### 真实输入

- `data/contracts/*` 下的合同文档属于真实业务输入 / 业务素材，应按资料资产看待，不是测试垃圾。

#### 需要额外谨慎的遗留物

- `data/legacy_jiusan_release_batches_backup.json` 属于 legacy 备份，当前没有看到它被代码直接引用；它不应被当成运行时真源。

### 4.3 备注

当前 `.gitignore` 已经把 `data/` 整体忽略了，但仓库里仍然存在已跟踪的 `data/*` 生成物，这说明历史上曾经把运行态文件提交进仓库；这属于边界债务，不是本轮要改的内容。

## 5. `chat_records/*`

### 5.1 真实位置

- `wx-ops-agent/data/chat_records`

### 5.2 当前内容

该目录存在，当前可见 20 个文件，主要分布在这些群：

- `铁晟业务工作群`
- `中唐特钢发运群`
- `数据单发群-GROUP013`
- `龙虾测试群`

主要结构：

- `YYYY-MM.jsonl`：聊天记录主账本
- `image_download_todos/YYYY-MM.jsonl`：图片待下载 / 重试待办
- `video_download_todos/YYYY-MM.jsonl`：视频待下载 / 重试待办

### 5.3 真实输入

- `YYYY-MM.jsonl` 是真实消息输入。
- 每行可见字段包括：
  - `seq`
  - `sender`
  - `sender-wxid`
  - `time`
  - `msg-type`
  - `msg-content`
  - `msg-path`
  - `remark`
- 其中 `msg-path` 里既有 `未下载`，也有真实本地图片路径。

### 5.4 测试垃圾 / 非生产噪音

- `龙虾测试群` 明显是测试群，不应当被当成生产业务输入。
- `.DS_Store` 不应纳入任何业务处理。
- `image_download_todos` / `video_download_todos` 是运行侧待办副产物，不是消息真源。

### 5.5 结论

- `chat_records` 是**真实输入层**，但其中混有测试群和 sidecar 待办文件。
- 消息真源是月度 JSONL，不是待办文件。

## 6. `wechat_images/*`

### 6.1 真实位置

- `/Users/qicai21/Documents/bussiness-artifacts/wechat_images`

### 6.2 当前内容规模

- 当前可见文件数：`3549`
- 目录包含：
  - `extractions/`
  - `projects/`
  - `reports/`
  - `other/`
  - `_migration_reports/`

### 6.3 真实输入 / 真实输出

#### 真实输入

- 原始图片文件 `*.jpg`
- 项目图片目录下的 `projects/**/images/**`

#### 真实输出

- `extractions/**/_result.json`
- `projects/**/json/**/_result.json`
- `projects/**/reports/*.xlsx`
- `reports/*.xlsx`
- `_migration_reports/*.json`

这些都属于运行期资产，不是仓库源码。

### 6.4 测试垃圾 / 非生产噪音

- `extractions/*smoke*`、`extractions/*resolve_smoke*` 这类文件是 smoke/test 产物。
- `.DS_Store` 不应纳入业务处理。

### 6.5 结论

- `wechat_images` 是**真实运行资产区**，不是源码区。
- 这里既有真实图片输入，也有 OCR/抽取 JSON、报表 XLSX 等派生输出。
- 本轮不需要把它改成 repo 内目录；它应保持在外部资产目录。

## 7. `logs/*`

### 7.1 真实位置

- `wx-ops-agent/data/runtime-logs`

### 7.2 当前内容

当前存在 2 个日志文件：

- `daemon-auto.log`
- `daemon-restart.log`

### 7.3 运行证据

日志首段可见：

- `WeChat Ops Agent Daemon started`
- `Cycle interval: 60s`
- `UI mode: auto`
- `Starting poll cycle for 3 groups`

这说明当前运行方式是 daemon 常驻轮询，而不是一次性脚本。

### 7.4 结论

- `logs/*` 应视为**纯运行态忽略项**。
- 不应入库、不应入 repo。

## 8. `.gitignore`

当前 `.gitignore` 已覆盖的关键边界：

- `data/`
- `*.db`
- `*.log`
- `.pytest_cache/`
- `.venv/`
- `__pycache__/`
- `dashboard/dispatch_board_data.json`
- `dashboard/jiusan_dashboard_data.json`

### 8.1 评估

- 方向是对的：运行态数据库、日志、缓存都被挡住了。
- 但它没有描述外部边界：
  - `wx-ops-agent/data/chat_records`
  - `wx-ops-agent/data/runtime-logs`
  - `/Users/qicai21/Documents/bussiness-artifacts/wechat_images`
- 这些边界目前主要靠代码约定和路径约定，而不是 repo 规则。

### 8.2 重要现象

- `dashboard/dispatch_board_data.json` 和 `dashboard/jiusan_dashboard_data.json` 虽然在 `.gitignore` 中，但已经在历史上被跟踪过；这属于“已跟踪的生成物”，不是干净的忽略边界。

## 9. 最终分类

### 应该 tracked

- 源码：`src/ops_hub/**`
- 测试：`tests/**`
- 订单 / 审计 / 设计文档：`orders/**`、`reports/**`、`docs/**`
- 业务资料中的合同文档：`data/contracts/**`（如果继续保留在仓库内）

### 应该 ignored

- `data/*.db`、`data/*.db-wal`、`data/*.db-shm`
- `wx-ops-agent/data/runtime-logs/*`
- 缓存：`__pycache__/`、`.pytest_cache/`、`.DS_Store`
- 生成 JSON / XLSX：`dashboard/dispatch_board_data.json`、`dashboard/jiusan_dashboard_data.json`、各类 preview 报表

### 真实输入

- `wx-ops-agent/data/chat_records/**/*.jsonl`
- `/Users/qicai21/Documents/bussiness-artifacts/wechat_images/projects/**/images/**`
- `data/contracts/**`

### 测试垃圾

- `龙虾测试群` 的 chat_records
- `extractions/*smoke*`
- `.DS_Store`
- `__pycache__`
- `.pytest_cache`
- 与本轮无关的 preview / smoke / 临时导出文件

## 10. 备注

- 本轮没有改 watcher。
- 本轮没有改数据库。
- 本轮没有做功能实现。
- 本轮只做边界审计。
