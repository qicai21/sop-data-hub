# 蓝鳍lot02 freight_detail 落库

**时间**: 2026-05-29 11:22
**操作**: enrich_release_batch (apply)

---

## 来源

| 字段 | 值 |
|---|---|
| 群 | 数据单发群-GROUP013 |
| seq | 53 |
| 时间 | 2026-05-29 11:22 |
| 文本 | "锦州港今日放货印粉3000吨, 入场合同号HNMC20260525-6X-1, 订单标识CGR20260526171655" |

## 绑定

| 字段 | 目标 |
|---|---|
| release_batch_id | 17860336195b8e6d419c2cc9993e3f1f2104f41e（蓝鳍 lot02） |

## 写入

| 字段 | 写入值 |
|---|---|
| contract_no | HNMC20260525-6X-1 |
| order_identifier | CGR20260526171655 |
| cargo_name_detail | 印粉 |
| quantity_tons | 3000 |

## 蓝鳍 lot1 vs lot2 对比

| | lot01 | lot02 |
|---|---|---|
| notice_date | 2026-05-21 | 2026-05-27 |
| contract_no | HNMC20260520-1X-1 | HNMC20260525-6X-1 |
| order_identifier | (空) | CGR20260526171655 |
| consignor | 锦州新德 | 锦州新儒 |
| cargo_name_detail | 印粉 | 印粉 |
| status | completed | in_progress |
| wagons | 46 | 0 |
