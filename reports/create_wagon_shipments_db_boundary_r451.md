# Report: R45.1 — Fix create_wagon_shipments DB boundary violation

| 字段 | 内容 |
|------|------|
| Order | R45.1 |
| 执行日期 | 2026-05-29 |
| Branch | codex/sop-real-sop-topology-audit-20260525 |
| Tests | 216 passed, 0 failed（+9 boundary tests） |

## 1. 结论

R45.1 完成。**create_wagon_shipments 不再写入 95306 DB**。所有写操作仅写入 sop_agent.db。

## 2. 修复内容

### Part A: create_wagon_shipments.py 修改

| 行 | 变更 | 说明 |
|----|------|------|
| 13-15 | docstring 修正 | 移除 "writes 95306 DB (INSERT shipment_release_batch_matches) if table exists" |
| 249 | **删除** | `rail_path = _resolve_rail_db_path(rail_db_path)` |
| 477-531 | **替换** | section 11: 原来打开 95306 DB 写连接 → 现在 `CREATE TABLE IF NOT EXISTS` + `INSERT INTO` sop_agent.db |

### Part B: db.py migration

| 位置 | 变更 |
|------|------|
| `migrate_shipment_release_batch_matches_schema()` | 新增：9列 + 2索引 |
| `open_db()` | 新增调用 |

## 3. 代码审计结果

### 修复后 create_wagon_shipments.py 的状态

```
$ grep -n "sqlite3.connect(str(rail_path))" create_wagon_shipments.py
→ NONE — clean

$ grep -n "mode=ro" create_wagon_shipments.py
→ NONE

$ grep -n "rail_path" create_wagon_shipments.py
→ NONE — clean

$ grep -n "shipment_release_batch_matches" create_wagon_shipments.py
13:  - reads 95306_collection.sqlite3 (shipment_release_batch_matches, read-only)  → docstring
15:  - writes sop_agent.db (INSERT shipment_release_batch_matches) — local table   → docstring
477: # ── 11. Write shipment_release_batch_matches to sop_agent.db ─
480: CREATE TABLE IF NOT EXISTS shipment_release_batch_matches (
499: INSERT INTO shipment_release_batch_matches (
```

**shipment_release_batch_matches 实际落在 sop_agent.db** ✅

### 其他模块的 95306 写连接（不在此次 scope）

| 文件 | 连接 | 说明 |
|------|------|------|
| inspection_95306_reconciler.py | `sqlite3.connect(str(rail_db_path))` | 检装车 reconciler 写入 95306 DB — 独立模块，不在此 scope |
| shipment_linkage.py | `sqlite3.connect(str(rail_db_path))` | 发运关联写入 — 独立模块 |
| dispatch_board.py | `file:…?mode=ro` | 只读 ✅ |
| shipment_status_sync.py | `sqlite3.connect(str(self.rail_db_path))` | 只读查询 ✅ |
| query_95306_shipments.py | `file:…?mode=ro` | 只读 ✅ |

## 4. sop_agent.db 新增表

```
shipment_release_batch_matches:
  id               TEXT PK
  release_batch_id TEXT NOT NULL
  wagon_shipment_id TEXT NOT NULL
  ydid             TEXT NOT NULL
  waybill_no       TEXT DEFAULT ''
  wagon_no         TEXT NOT NULL
  container_no     TEXT DEFAULT ''
  match_source     TEXT DEFAULT 'departure_text_match'
  created_at       TEXT DEFAULT CURRENT_TIMESTAMP
```

索引：
- `idx_sop_matches_batch` ON release_batch_id
- `idx_sop_matches_wagon` ON wagon_shipment_id

## 5. 测试结果

### R45.1 边界测试（9 tests，全部通过）

| # | 测试 | 状态 |
|---|------|:--:|
| 1 | 95306 ships 行数 apply 前后不变 | PASS |
| 2 | 95306 shipment_release_batch_matches 行数不变 | PASS |
| 3 | sop_agent.db 中存在 shipment_release_batch_matches 表 | PASS |
| 4 | match 记录写入 sop_agent.db | PASS |
| 5 | wagon_shipments 写入 sop_agent.db | PASS |
| 6 | 95306 DB 文件校验和不变 | PASS |
| 7 | 95306 DB 不存在不崩溃 | PASS |
| 8 | 重复 apply 幂等（wagon + match 都不重复） | PASS |
| 9 | match 中 wagon_shipment_id FK 正确 | PASS |

## 6. 修改文件

| 文件 | 变更 |
|------|------|
| src/ops_hub/sop/create_wagon_shipments.py | -3行（rail_path）+ 替换 section 11（95306 write → sop write）|
| src/ops_hub/data_agent/db.py | +migrate_shipment_release_batch_matches_schema() |

## 7. 新增文件

| 文件 | 用途 |
|------|------|
| tests/functional/test_create_wagon_shipments_db_boundary_r451.py | 9 boundary tests |
| reports/create_wagon_shipments_db_boundary_r451.md | 本报告 |

## 8. R45 遗留的 rail_db_path 参数

```
create_wagon_shipments_from_candidates(..., rail_db_path=...)  → 参数保留，但内部不再使用
_resolve_rail_db_path()                                      → 函数定义保留，但不再调用
```

这是有意保留的 — 参数签名稳定化，避免调用方被迫改名。内部代码不再使用这些参数。
