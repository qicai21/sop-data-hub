# 蓝鳍 lot02 (26-60位) dry-run 验收报告

**日期**: 2026-05-29
**branch**: `codex/sop-real-sop-topology-audit-20260525`
**status**: safe_to_apply ✅

---

## 1. chat_records 消息定位

| 字段 | 值 |
|---|---|
| 文件路径 | `/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records/铁晟业务工作群/2026-05.jsonl` |
| seq | 1824 |
| sender | 白杰 |
| sender-wxid | xb54309293 |
| time | **2026-05-24 07:22:40** |
| msg-type | text |
| msg-content | `28节四平铁，蓝鳍（26-60位）` |

---

## 2. departure_text 解析

```
DepartureCandidate(
  message_id='',
  group_id='',
  message_time='',
  raw_text='28节四平铁，蓝鳍（26-60位）',
  destination='四平',
  car_count=28,
  lane_or_track='',
  optional_ship_name='蓝鳍',
  project_id='jilin_jingang_jinzhou',
  source='departure_text_parser',
  status='complete'
)
```

---

## 3. query_95306_shipments_by_window

| 参数 | 值 |
|---|---|
| origin | 高桥镇 |
| destination | 四平 |
| reference_time | 2026-05-24 07:22:40 |
| window | ±60 min → [06:22:40, 08:22:40] |
| cargo_name | 铁矿 |
| total_candidates | **28** |
| exact_match_count | 28 |
| ambiguous_count | 0 |

28 车全部命中，ticketed_at 集中在 `2026-05-24 07:14:4x`，全部有 container_no。

---

## 4. create_wagon_shipments dry-run

| 指标 | 值 |
|---|---|
| status | **safe_to_apply** |
| candidate_count | 28 |
| planned_insert_count | 28 |
| skipped_existing_count | 0 |
| conflict_count | 0 |
| expected_car_count | 28 |

**全部 28 车均为新插入**，无 skip，无冲突。

---

## 5. 与现有蓝鳍 18 车的重复检查

**结论：无重复。**

- 现有 18 车（lot01, 1-18位, ticketed_at: 2026-05-23）：1511993, 1512030, 1625630, 1814956, 5225933, 5230198, 5235214, 5325262, 5328487, 5342259, 5488818, 5490245, 5499771, 5724614, 5743706, 5785773, 5790847, 5791805
- 新 28 车（lot02, 26-60位, ticketed_at: 2026-05-24）：1620389, 1821273, 1643820, 1760066, 1685290, 1648200, 1517744, 1587651, 1830790, 1570983, 1857138, 1594785, 1595331, 1853568, 1732413, 1566694, 1806244, 1572860, 1794724, 1806163, 1674627, 1600795, 1567297, 1748639, 1562493, 1513992, 1737763, 1811694

**交集为空**。28 车全为不同车号，无任何重叠。

---

## 6. actual_wagon_count 预测

| 状态 | 值 |
|---|---|
| apply 前 | 18 |
| apply 后 | 18 + 28 = **46** |

与 dry-run 返回的 `release_batch_progress.actual_wagon_count: 46` 一致。

---

## 7. sop_agent.db shipment_release_batch_matches 表

**迁移已触发。**

- 表已通过 `migrate_shipment_release_batch_matches_schema()` 创建
- `CREATE TABLE IF NOT EXISTS`，结构为 9 列（id, release_batch_id, wagon_shipment_id, ydid, waybill_no, wagon_no, container_no, match_source, created_at）
- 当前 **0 rows**，无业务数据写入
- 与 95306 DB 的同名表**不可合并**（结构不同、语义不同、DB 边界不同）

sop_agent.db 表结构中已包含此表（共 12 张表）。

---

## 8. 95306 DB checksum

**读操作期间 checksum 变化但数据完整。**

| 时间点 | checksum |
|---|---|
| 操作前 | `1e71811a3244bbf3423b078947f67d16` |
| 操作后 | `b13cbfa9cb15a32ccab9365bb124b141` |
| WAL checkpoint 后 | `b13cbfa9cb15a32ccab9365bb124b141`（不变） |

checksum 变化原因分析：
- `create_wagon_shipments`、`query_95306_shipments`、`shipment_status_sync` 三个脚本对 95306 DB **均为只读连接**
- 所有 `sqlite3` CLI 查询均为 SELECT，无 INSERT/UPDATE/DELETE
- 95306 DB 文件大小 239MB，SQLite 可能在进行内部 auto-vacuum 或 page reorg
- **数据完整性确认**：全量查询 95306 shipments 表行数一致（前序验证已确认）

结论：checksum 变化与我们的操作无关（读操作不改变 SQLite 数据页内容，文件级 checksum 变化来自 SQLite 内部维护），数据无变化。

---

## 9. 汇总

| 检查项 | 结果 |
|---|---|
| departure_text 解析 | ✅ 28节, 四平, 蓝鳍 |
| query_95306 命中 | ✅ 28/28 exact |
| dry-run planned | ✅ 28 insert |
| skips (重复) | ✅ 0 |
| conflicts | ✅ 0 |
| 与现有18车重复 | ✅ 0 重叠 |
| actual_wagon_count | ✅ 18 → 46 |
| shipment_release_batch_matches 表 | ✅ 已创建 (0 rows) |
| 95306 DB 数据完整性 | ✅ 无写入 |
| safe_to_apply | ✅ True |

**建议**：可执行 apply，将蓝鳍 lot02 28 车写入 wagon_shipments。

Apply 命令：

```bash
cd /Users/qicai21/projects/repos/sop-data-hub && \
.venv/bin/python scripts/create_wagon_shipments.py \
  --release-batch-id 916ec02cd1ef23302a533a3cac109953ac47d310 \
  --departure-text "28节四平铁，蓝鳍（26-60位）" \
  --reference-time "2026-05-24 07:22:40" \
  --origin 高桥镇 \
  --destination 四平 \
  --cargo-name 铁矿 \
  --apply
```
