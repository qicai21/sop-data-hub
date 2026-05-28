# R33: Implement 吉林金钢 departure text parser

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Task:** P0-3 — Implement departure text parser

---

## 背景

SOP departure_flow.detect_departure_message 要求解析发运文本：

```
"6道，四平铁，46车"
"十四道，朝阳西铁矿，木森17，装55节"
```

提取 message_time, destination, car_count, optional_ship_name。本轮只实现解析器，不接 95306。

## 实现

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/ops_hub/sop/departure_text_parser.py` | 发运文本解析器 + `DepartureCandidate` dataclass |
| `tests/functional/test_departure_text_parser.py` | 19 tests |

### DepartureCandidate

```python
@dataclass(frozen=True)
class DepartureCandidate:
    message_id: str
    group_id: str
    message_time: str           # from MessageEvent.received_at
    raw_text: str               # original message
    destination: str            # canonical: 四平/朝阳西/汐子
    car_count: int              # -1 = unparseable → incomplete
    lane_or_track: str          # "6道", "十四道"
    optional_ship_name: str     # known ship name
    project_id: str             # mapped from destination
    source: str                 # "departure_text_parser"
    status: str                 # complete | incomplete | no_match
```

### 解析规则

| 组件 | 正则/逻辑 | 示例 |
|------|----------|------|
| lane_or_track | `(\d+\|十\S*?)道` | 6道, 十四道 |
| car_count | `(?:装\s*)?(\d{1,3})\s*(?:车\|节)` | 46车, 装55节 |
| destination | 别名映射表 → canonical | 四平铁→四平, 沙子→汐子 |
| ship_name | 已知船名集合 | 长航滨海, 蓝鳍, 木森17, 贝拉 |
| project_id | destination→project 映射 | 四平→jilin_jingang_jinzhou |

### 状态逻辑

```
有 destination/lane/car_count 任一 → 是发运文本
  car_count ≥ 0 → status = "complete"
  car_count < 0 → status = "incomplete"
全无 → status = "no_match"
```

## 支持的文本样式

| 样式 | destination | car_count | lane | ship | project |
|------|:----------:|:---------:|:----:|:----:|---------|
| `6道，四平铁，46车` | 四平 | 46 | 6道 | — | jilin_jingang |
| `十四道，四平铁，蓝鳍，18车` | 四平 | 18 | 十四道 | 蓝鳍 | jilin_jingang |
| `6道，四平方向，长航滨海，46车` | 四平 | 46 | 6道 | 长航滨海 | jilin_jingang |
| `四平，46车` | 四平 | 46 | — | — | jilin_jingang |
| `十四道，朝阳西铁矿，木森17，装55节` | 朝阳西 | 55 | 十四道 | 木森17 | chaoyang_steel |
| `6道，朝阳铁，32车` | 朝阳西 | 32 | 6道 | — | chaoyang_steel |
| `汐子，贝拉，48车` | 汐子 | 48 | — | 贝拉 | zhongtang |
| `沙子，28车` | 汐子 | 28 | — | — | zhongtang |

## 边界情况

| 输入 | status | 原因 |
|------|:------:|------|
| `6道，四平方向` | incomplete | 缺 car_count |
| `四平方向` | incomplete | 缺 car_count |
| `今天天气不错` | no_match | 无发运特征 |
| `订单标识 CGR...` | no_match | freight_detail_text，非发运 |
| `长航滨海` | no_match | 只有船名，无发运特征 |
| `''` | no_match | 空文本 |

## 测试

```
pytest tests/functional/test_departure_text_parser.py -v
19 passed

pytest tests/functional -v
74 passed (48 legacy + 7 R32 + 19 R33)
```

### 测试覆盖

1. `test_py_6dao_siping_46cars` — 基础格式 "6道，四平铁，46车"
2. `test_py_14dao_siping_lanqi` — 含船名 "十四道，蓝鳍，18车"
3. `test_py_6dao_siping_direction_changhang` — "四平方向" 别名 + ship
4. `test_py_siping_only_keyword` — 纯关键词 "四平，46车"
5. `test_py_missing_car_count_incomplete` — 缺车数 → incomplete
6. `test_py_no_count_no_lane_still_incomplete` — 只有 destination → incomplete
7. `test_py_irrelevant_text_no_match` — 无关文本
8. `test_py_freight_detail_no_match` — freight_detail 不误命中
9. `test_py_chaoxi_departure` — 朝阳格式 "装55节"
10. `test_py_chaotie_alias` — "朝阳铁" → 朝阳西
11. `test_py_shizi_departure` — 汐子格式
12. `test_py_shazi_alias` — "沙子" → 汐子 (OCR alias)
13. `test_py_from_message_event` — MessageEvent 集成
14. `test_py_empty_text_no_match` — 空文本
15. `test_py_to_dict` — to_dict() 输出
16. `test_py_53_cars` — 53车 (SOP pattern)
17. `test_py_55_cars` — 55车 (SOP pattern)
18. `test_py_zhuang_55_jie` — "装55节" 格式
19. `test_py_ship_name_without_cars_incomplete` — 纯船名 → no_match

## 未做

- 不查 95306
- 不写 wagon_shipments
- 不生成 Excel / JSON
- 不发送报告
- 不改 DB schema
- 不改 wx-ops-agent
- 不改 jilin_jingang.yaml

## 下一步：build_95306_query_window

下一节点 `build_95306_query_window` 将使用 `DepartureCandidate` 的：

- `message_time` → 构造 `start = message_time - 60m`, `end = message_time + 60m`
- `destination` → 查询 95306 的 `destination_name` 过滤条件
- `car_count` → 验证 95306 返回的车辆数是否匹配
- `project_id` → 路由到对应的 reconciler

当前 `inspection_95306_reconciler.py` 的 `_query_all_shipments_in_window()` 可复用，需改造为接受 `message_time` 驱动的窗口（目前基于 `ticketed_at`）。

## 文件

- `src/ops_hub/sop/departure_text_parser.py` — 新
- `tests/functional/test_departure_text_parser.py` — 新
