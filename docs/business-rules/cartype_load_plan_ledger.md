# 各项目车型装货配载吨台账

机器读:`config/cargo_load_plans.yaml`
本档:人读 + 变更历史

## 业务含义

| 量 | 来源 | 用途 |
|---|---|---|
| **配载吨**(本台账) | 业务/调度跟港口确认下达的"应装" | 装车工人的目标 |
| **标载吨** | 95306 `marked_weight` 实际称重 | 铁路下账 |
| **放货计划吨** | `release_batches.batch_quantity`(出港计划通知单) | 船代下达的总量 |

三方对账:
- 涨吨 = 标载 − 配载(>0 = 装多了,常见原因:水分/磅差)
- 超装 = 标载 − 放货计划(>0 = 总量超船代下达)

## 项目台账

### chaoyang_steel(朝阳钢铁)

铁矿散运,锦州港 → 朝阳西,**整车散运**。

| 匹配 | 配载吨 | 生效起 | 备注 |
|---|---|---|---|
| 车型 C70 系(C70/C70E/C70EH/C70H) | **69.0** | 2026-06-01 | 用户跟港口确认 |
| 标载 = 61 t 的车型(C64K/C64H) | **63.5** | 2026-06-01 | 用户口径 |
| 标载 = 60 t 的车型(C62AK 等) | **62.5** | 2026-06-01 | 用户口径 |

### zhongtang_special_steel(中唐特钢)

铁矿粉散运,锦州港 → 汐子,**整车散运**。

> **⚠️ 2026-07-21 口径统一:中唐不再单列配载吨。**
> 中唐装车重量口径统一为「实装」单一真相,唯一来源是
> `config/project_sops/zhongtang.yaml → project_meta.shipped_weight_rule`:
>
> | 标载 | 实装吨 |
> |---|---|
> | 70 | **70.2** |
> | 61 | **64.5** |
> | 60 | **63.5** |
>
> 该 rule 是**在跑的活配置**(shipped_weight.py / create_wagon_shipments.py / wagon_ingest.py /
> billing_generate_zhongtang_handling.py 读取)。原配载计划口径(C70=70.0/61=64.5/60=63.5)
> 已从 `cargo_load_plans.yaml` 删除,避免与实装口径混淆。

### jilin_jingang_jinzhou(吉林金钢)

铁矿运输,锦州港 → 四平,**集装箱业务**。

| 匹配 | 配载吨 | 生效起 | 备注 |
|---|---|---|---|
| 每个 box(箱) | **32.8** | 2026-06-01 | 用户口径,box 维度 |

注:每辆车 2 box,所以每车 = 65.6 吨;集装箱业务 box 是结算单位。

## 同档朝钢/中唐对比表

| 档 | 朝钢 | 中唐 | 差 |
|---|---|---|---|
| C70 系 | 69.0 | 70.0 | +1.0 |
| 标载 61 | 63.5 | 64.5 | +1.0 |
| 标载 60 | 62.5 | 63.5 | +1.0 |

## 变更历史

| 日期 | 项目 | 变更 | 来源 |
|---|---|---|---|
| 2026-06-12 v1 | chaoyang_steel | 初始 C70=69.5 / C60=63 | 早期口径 |
| 2026-06-12 v2 | chaoyang_steel | 改 C70=69 / 标载61=63.5 / 标载60=62.5 | 用户跟港口确认 |
| 2026-06-12 v1 | zhongtang_special_steel | 初始 C70=69 / 标载61=63.5 / 标载60=62.5 | 早期口径 |
| 2026-06-12 v2 | zhongtang_special_steel | 改 C70=70 / 标载61=64.5 / 标载60=63.5 | 用户口径 |
| 2026-06-12 v1 | jilin_jingang_jinzhou | 新增 32.8 t/box | 用户口径 |
| 2026-07-21 | zhongtang_special_steel | 删除配载计划口径,统一为实装 shipped_weight_rule(70.2/64.5/63.5) | 用户要求,避免混淆 |

## 程序读取约定

```yaml
projects:
  <project_id>:
    unit: ton_per_wagon  | ton_per_box
    car_load_plans:                  # ton_per_wagon 项目
      - car_model_pattern: "^C70"
        load_tons: 69.0
      - marked_weight_eq: 61
        load_tons: 63.5
    box_load_plan:                   # ton_per_box 项目
      load_tons: 32.8
```

匹配优先级:**car_model_pattern → marked_weight_eq → fallback**。

## 关联

- [[business-architecture-core]] 项目结构
- [[regular-freight-no-circulation]] 普通货运无循环
- 实现待补:`src/sop_hub/calc/cargo_load_plan.py`(预留)
