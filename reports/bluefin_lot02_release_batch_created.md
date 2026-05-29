# 蓝鳍 lot02 release_batch 落库报告

**日期**: 2026-05-29
**branch**: `codex/sop-real-sop-topology-audit-20260525`
**status**: ✅ 落库完成

---

## 落库信息

| 字段 | 值 |
|---|---|
| id | `17860336195b8e6d419c2cc9993e3f1f2104f41e` |
| batch_key | `蓝鳍\|铁矿\|锦州新儒物流有限公司\|锦州新儒物流有限公司\|四平\|lot02` |
| ship_name | 蓝鳍 |
| notice_date | **2026-05-27** |
| batch_sequence | lot02 |
| batch_quantity | 3000.0 |
| consignor | 锦州新儒物流有限公司 |
| destination_station | 四平 |
| origin_station | 高桥镇 |
| yard_location | 311W场地 |
| project | 吉林金钢-锦州港铁矿发运项目 |
| dispatch_status | in_progress |
| batch_count | 0 |

---

## 落库方式

直接 INSERT 到 `sop_agent.db`，字段来源为 wx-ops-agent OCR 提取结果（confidence 0.95）。

id 通过 `sha1(batch_key)` 生成，与 wx-ops-agent 原始 ingestion 产生的 `_agent_updated_ids` **完全一致**，说明此前该记录已被创建过但丢失。

以下字段图片中未标注，留空：
- contract_no
- plan_id
- order_identifier
- cargo_product_name
- agent_name
- customer_name

---

## 蓝鳍当前状态

| lot | release_batch_id | notice_date | quantity | wagons | status |
|---|---|---|---|---|---|
| lot01 | 916ec02...47d310 | 2026-05-21 | 3000 | 46 | delivered |
| lot02 | 17860336...f2104f41e | **2026-05-27** | 3000 | 0 | in_progress |

---

## 下一步

lot02 尚无 wagon_shipments。需等待 departure_text（白杰或 Li. 报告"xx节四平铁蓝鳍（xx位）"）后通过 `create_wagon_shipments` 落车，再通过 `shipment_status_sync` 同步交付状态。
