# R71: 吉林金钢 wx_2206 真实全链路回放

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Repo:** `qicai21/sop-data-hub`
**Parent:** R70 (`f598988`)

---

## 1. 清理前后统计

| 项 | 清理前 | 清理后 |
|---|---|---|
| wagon_shipments | 243 (45 wx_2206) | 198 |
| shipment_release_batch_matches | 73 | 28 |
| workflow_task_db | 12 | 12 (id=1 reset→pending) |
| external_action_log | 3 | 0 (recreated during replay) |
| message_inbox wx_2206 | task_succeeded | matched_sop |

---

## 2. 真实回放结果

**release_batch_id:** `17860336195b8e6d419c2cc9993e3f1f2104f41e` (蓝鳍)

| 步骤 | 结果 |
|---|---|
| 1. parse_departure_text | ✅ complete: 煤六, 45车, 四平, 蓝鳍 |
| 2. query_95306 | ✅ 45/45 exact match |
| 3. create_wagon_shipments | ✅ **45 inserted** |
| 4. departure_excel | ✅ 45 rows, 45 wagons |
| 5. factory_upload | ✅ login=true, 90 payloads, 90 success, 0 failure |
| 5b. factory_verify | ✅ verified=true, total_match=true, boxes_ok=true |
| 6. send_excel_wechat | ✅ sent=true, target=郭东北 |

**wagon_insert_count:** 45 (from 0 → 45 new insertions)

**Excel path:** `/Users/qicai21/projects/repos/sop-data-hub/output/excel/吉林金钢_发运数据_20260531_45车.xlsx`

---

## 3. External Action Log

| action_type | status | idempotency_key |
|---|---|---|
| generate_shipping_excel | **executed** | jilin_jingang_jinzhou:generate_shipping_excel:1786033...:45 |
| factory_upload_submit | **executed** | jilin_jingang_jinzhou:factory_upload_submit:1786033...:45 |
| send_shipping_excel_wechat | **executed** | jilin_jingang_jinzhou:send_shipping_excel_wechat:1786033...:45 |

---

## 4. 幂等回放验证

| 项 | 结果 |
|---|---|
| external_actions | planned=0, skipped_dup=3, effective_apply=False |
| wagon inserts | 0 (45 skip_existing) |
| Excel 再生 | 否 (not generated) |
| 工厂上传 | 否 (login=False, success=0) |
| 微信发送 | 否 (sent=False) |

**✅ idempotency gate: external_action_log executed → 阻止重复外发**

---

## 5. 最终状态

| 项 | 状态 |
|---|---|
| message_inbox wx_2206 | task_succeeded ✅ |
| workflow_task_db id=1 | succeeded ✅ |
| wagon_shipments | 243 (45 new wx_2206) ✅ |
| shipment_release_batch_matches | 73 ✅ |
| external_action_log | 3 executed ✅ |
| 95306_collection.sqlite3 | 未写入 ✅ (5GB, last mod 16:21, executor ran 16:25) |
| live_service | alive=true, PID 94390, cursor 9 sources ✅ |

---

## 6. 修改文件

| 文件 | 操作 |
|---|---|
| `src/ops_hub/sop/workflow_task_executor.py` | R71 idempotency gate: 检查 external_action_log 已执行→effective_apply=False；自动标记 executed |

---

## 7. Commit

**`63e8b69`**
