# 蓝鳍第二次放货通知单 dry-run 报告

**日期**: 2026-05-29
**branch**: `codex/sop-real-sop-topology-audit-20260525`
**status**: 原始图片已找到，OCR 已完成，release_batch **未落库**

---

## 1. 原始图片消息定位

| 字段 | 值 |
|---|---|
| **群** | 铁晟业务工作群 |
| **chat_records 文件** | `/Users/qicai21/projects/repos/wx-ops-agent/data/chat_records/铁晟业务工作群/2026-05.jsonl` |
| **seq** | **2052** |
| **sender** | 赛彬 (wxid_23ngoo04542c21) |
| **time** | **2026-05-27 11:13:40** |
| **msg-type** | image |
| **msg-content** | (image — 前一消息 seq 2051 文本: "四平放货3000吨蓝鳍") |
| **原始 msg-path** | `/Users/qicai21/Documents/bussiness-artifacts/wechat_images/铁晟业务工作群/2026-05/2303_334f816dcf19328df3addc995e6e488b.jpg` |

✅ **消息已确认，有前一文本消息 + 紧随图片消息。**

---

## 2. 图片文件状态

| 路径类型 | 路径 | 存在 |
|---|---|---|
| 原始路径 (chat_records) | `.../2026-05/2303_334f816dcf19328df3addc995e6e488b.jpg` | ❌ 已归档 |
| 分类目录 (出港计划通知单) | `.../出港计划通知单/2303_334f816dcf19328df3addc995e6e488b.jpg` | ✅ |
| 项目归档 (lot02) | `.../projects/吉林金钢-锦州港铁矿发运项目/四平/蓝鳍/lot02/images/2026-05-27/2303_334f816dcf19328df3addc995e6e488b.jpg` | ✅ |
| OCR 提取 JSON | `.../projects/.../lot02/json/2026-05-27/2303_334f816dcf19328df3addc995e6e488b_result.json` | ✅ |
| _status JSON | `.../铁晟业务工作群/_status/2303_334f816dcf19328df3addc995e6e488b.json` | ✅ |

✅ **图片已被 wx-ops-agent 自动分类到 `出港计划通知单` → `吉林金钢/四平/蓝鳍/lot02`，OCR 已完成。**

---

## 3. OCR 解析结果 (dry-run)

来源: `2303_334f816dcf19328df3addc995e6e488b_result.json`，confidence 0.95。

```yaml
title: 锦州港货物出港计划通知单
is_target: true
project: 吉林金钢-锦州港铁矿发运项目

header_info:
  通知日期: 2026年05月27日    # ← lot1 是 05月21日
  内、外贸: 外贸

business_info:
  发货单位: 锦州新儒物流有限公司   # ← lot1 是 锦州新德 (不同!)
  收货单位: 锦州新儒物流有限公司
  船名: 蓝鳍
  接货库场: 集团库
  联系人: 李鑫
  电话: 15141635282

cargo_info:
  货物名称: 铁矿
  包装: 散
  总重里: 3000                    # (吨)
  运输方式: 铁路

special_matter: |
  发运"蓝鳍"轮所卸货物，货物在311W场地，
  港方负责装火车，装车前过磅，火运敞顶箱出港，
  到站：四平。海关放行单：55139吨

remarks:
  - date: "5月21日"
    sequence: "第二次下达计划"
    plan: "3000吨（铁路 四平）"
  - date: "5月27日"
    sequence: "第二次下达计划"
    plan: "3000吨（铁路 四平）"
```

### 关键字段提取

| 字段 | lot01 (已存在) | lot02 (本次) | 差异 |
|---|---|---|---|
| notice_date | 2026-05-21 | **2026-05-27** | ✅ 不同 |
| ship_name | 蓝鳍 | 蓝鳍 | 同 |
| cargo_name | 铁矿 | 铁矿 | 同 |
| cargo_product_name | 印粉 | (未标注) | — |
| batch_quantity | 3000 | 3000 | 同 |
| origin_station | 高桥镇 | 高桥镇 (311W场地) | 同 |
| destination_station | 四平 | 四平 | 同 |
| consignor | 锦州新德物流有限公司 | **锦州新儒物流有限公司** | ⚠️ 不同 |
| consignee | 锦州新德物流有限公司 | **锦州新儒物流有限公司** | ⚠️ 不同 |
| contract_no | HNMC20260520-1X-1 | (无) | — |
| plan_id | CGR20260520095954 | (无) | — |
| order_identifier | (空) | (无) | — |
| customs_release_qty | 55139 | 55139 | 同 |
| remarks | 第一次下达计划 | 第二次下达计划 | ✅ 不同 |
| trade_type | 外贸 | 外贸 | 同 |
| transport_mode | 铁路 | 铁路 | 同 |

---

## 4. sop_agent.db 现状

| 项目 | 值 |
|---|---|
| 蓝鳍现有 release_batches | **1 条** (lot01) |
| lot01 batch_key | `蓝鳍\|铁矿\|锦州新德物流有限公司\|锦州新德物流有限公司\|四平\|lot01` |
| lot02 预期 batch_key | `蓝鳍\|铁矿\|锦州新儒物流有限公司\|锦州新儒物流有限公司\|四平\|lot02` |
| lot02 是否存在 | **❌ 不存在** |

extraction JSON 中记录的 `_agent_updated_ids: ["17860336195b8e6d419c2cc9993e3f1f2104f41e"]` 在 `release_batches` 表中**不存在**。推测 wx-ops-agent 的 ingestion pipeline 产生了 `release_batch_upsert` 但该记录因未知原因未持久化或被删除。

---

## 5. 判断：是否应创建蓝鳍 lot2

**✅ 应该创建新的 release_batch，不更新 lot01。**

理由：

1. **日期不同**：lot01 notice_date=05-21，lot02=05-27。同一船的不同放货批次。
2. **发货/收货单位不同**：lot01 是 锦州新德，lot02 是 锦州新儒。这直接影响 batch_key 的唯一性（consignor 是 batch_key 的组成部分）。
3. **remarks 标注为"第二次下达计划"**：图片上明确写了"第二次下达计划：3000吨（铁路 四平）"。
4. **已有 wagon_shipments 分离**：lot01 已有 18+28=46 车 wagon_shipments（1-18位 + 26-60位），这些批次已全部交付。lot02 对应的车次（如果有）应是新的 departure_text。
5. **新 batch_key 不会冲突**：consignor 不同 ⇒ batch_key 不同 ⇒ 不会与 lot01 冲突。

---

## 6. planned_create_release_batch (dry-run only)

**不写库，仅计划。**

```yaml
planned:
  action: create_release_batch
  batch_key: "蓝鳍|铁矿|锦州新儒物流有限公司|锦州新儒物流有限公司|四平|lot02"
  
  fields:
    ship_name: 蓝鳍
    cargo_name: 铁矿
    batch_quantity: 3000.0
    total_planned_quantity: 3000.0
    notice_date: 2026-05-27
    batch_sequence: lot02
    batch_count: 0                  # 待 wagon_shipments 写入后更新
    origin_station: 高桥镇
    destination_station: 四平
    consignor: 锦州新儒物流有限公司
    consignee: 锦州新儒物流有限公司
    trade_type: 外贸
    transport_mode: 铁路
    yard_location: 311W场地
    customs_release_qty: 55139.0
    project: 吉林金钢-锦州港铁矿发运项目
    dispatch_status: in_progress
    source_file_name: 2303_334f816dcf19328df3addc995e6e488b.jpg
    source_json: (OCR extraction result)
    
  # 以下字段图片中未标注，留空
    contract_no: ""
    plan_id: ""
    order_identifier: ""
    cargo_product_name: ""
    agent_name: ""
    customer_name: ""
```

---

## 7. 结论

| 问题 | 答案 |
|---|---|
| 是否找到原始图片消息 | ✅ seq 2052, 赛彬, 2026-05-27 11:13:40 |
| 图片路径是否存在 | ✅ 已归档到 lot02/images/ |
| OCR 是否能解析 | ✅ wx-ops-agent 已完成，confidence 0.95 |
| 是否建议创建蓝鳍 lot2 | ✅ yes — notice_date、consignor、remarks 均不同 |
| 是否需要用户重新发送图片 | ❌ 不需要，图片已归档且 OCR 已完成 |
| release_batches 落库状态 | ❌ lot02 未落库，需手动创建 |

---

**建议下一步**：由 #执行模式 创建蓝鳍 lot02 release_batch，不做 wagon 关联。
