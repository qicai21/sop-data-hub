# R40: SOP enrichment — freight_detail_extractor + departure_text_parser 增强

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Type:** SOP 规则更新 + 新增 Parser — 不新增 DB 写入或报表生成

---

## 1. 状态确认

```
live_service --status: source_of_truth=git, loaded_projects=4, jilin_jingang_jinzhou ✅
pytest tests/functional -v: 148 passed, 0 failed
```

---

## 2. Part A: SOP YAML 规则更新

### 2.1 freight_detail_flow（第 274-310 行）

**新增：**

```yaml
text_patterns:
  - "订单标识"       # 强标识关键词（MUST have，否则 no_match）
  - "入厂合同号"      # 变体
  - "入场合同号"      # 变体
  - "放货"           # 弱关键词

extract_fields:
  - "port"           # 港口名（锦州港/鲅鱼圈港）
  - "quantity_tons"  # 吨数

binding_rule: "manual_only"
binding_reason: "freight_detail_text 不含船名，无法自动绑定到 release_batch。需 Agent 或人工指定绑定。"

no_auto_bind:
  - "release_batch"
  - "ship_name"
```

### 2.2 departure_flow（第 298-340 行）

**新增 5 个实际文本示例：**

| 文本 | 特点 |
|------|------|
| `28节四平铁，蓝鳍（26-60位）` | 车数在前，位置范围在括号内 |
| `煤六   四平铁"蓝鳍"18节` | 中文引号包围船名，`节` 等同于 `车` |
| `九道   四平镍"长航滨海"46节` | 四平镍（新目的地别名），中文引号 |
| `十四道 41节 四平铁 厦门世纪` | 船名在车数后 |
| `煤六 39节 四平铁 智慧` | 船名在最后 |

**新增语义规则：**

```yaml
car_units: '"车" 和 "节" 等价为 car_count'
ship_name_position: "船名可能在车数前，也可能在车数后"
ship_name_quotes: "船名可能被中文引号包裹"
lane_or_track: '"煤六"、"九道"、"十四道" 等为 lane_or_track'
position_range: '括号内 "26-60位" 保留为 raw_text，不作为独立字段'
```

---

## 3. Part B: freight_detail_extractor.py（新增）

### 3.1 模块

**文件:** `src/ops_hub/sop/freight_detail_extractor.py`（188 行）

### 3.2 数据模型

```python
@dataclass(frozen=True)
class FreightDetailCandidate:
    message_id: str
    group_id: str
    message_time: str
    raw_text: str
    project_id: str           # "jilin_jingang_jinzhou"
    port: str                 # 锦州港 / 鲅鱼圈港
    cargo_name_detail: str    # 印粉 / mb粉
    quantity_tons: int        # 3000 / 2000
    contract_no: str          # HNMC20260520-1X-1
    order_identifier: str     # CGR20260520095954
    status: str               # complete | incomplete | no_match
    binding_status: str       # "needs_manual_binding"（固定）
    source: str               # "freight_detail_extractor"
```

### 3.3 核心逻辑

**guard：** `订单标识` 是强关键词 — 没有它返回 `no_match`。

**keyword matching：**

```
"订单标识CGR20260520095954" → order_identifier = "CGR20260520095954"
"入场合同号HNMC20260520-1X-1" → contract_no = "HNMC20260520-1X-1"
"放货印粉3000吨" → cargo_name_detail = "印粉", quantity_tons = 3000
"锦州港今日" → port = "锦州港"
```

**binding_status：**
- freight_detail_text 不含船名 → `binding_status = "needs_manual_binding"`
- 系统 MUST NOT 自动绑定到 release_batch
- 输出为 `FreightDetailCandidate`，由 Agent 或人工指定绑定

### 3.4 测试

**文件：** `tests/functional/test_freight_detail_extractor.py`（10 tests）

| 测试 | 输入 | 期望结果 |
|------|------|---------|
| test_jinzhou_yinfen_3000t | 锦州港放货印粉3000吨, 入场合同号HNMC20260520-1X-1, 订单标识CGR20260520095954 | complete: port=锦州港, cargo=印粉, qty=3000, contract_no=HNMC20260520-1X-1, order=CGR20260520095954 |
| test_bayuquan_mbfen_2000t | 鲅鱼圈港放货mb粉2000吨入厂合同号XYSJLJR25KF1222-01-1，订单标识CGR20251222174305 | complete: port=鲅鱼圈港, cargo=mb粉, qty=2000 |
| test_no_order_identifier_no_match | 锦州港放货印粉3000吨, 合同号HNMC20260520-1X-1 | no_match (缺 订单标识) |
| test_departure_text_not_matched | 6道，四平铁，46车 | no_match (发运文本不被误匹配) |
| test_binding_status_always_needs_manual | (任意货运文本) | needs_manual_binding |
| test_only_order_identifier_incomplete | 订单标识CGR20260601000001 | incomplete |
| test_from_message_event | MessageEvent | 字段完整复制 |
| test_to_dict | — | 可序列化 |
| test_empty_text_no_match | "" | no_match |

---

## 4. Part C: departure_text_parser.py 增强

### 4.1 新增船名

```python
_KNOWN_SHIPS = {
    ...,
    "厦门世纪",
    "智慧",
}
```

### 4.2 新增目的地别名

```python
"四平镍": "四平",  # 四平镍 → 四平
```

### 4.3 车道/轨道识别增强

**之前：** `\d+道` 或 `十...道`

**之后：**
- `煤六`（不强制带 "道"）→ lane="煤六"
- `九道` → lane="九道"
- `十四道` → lane="十四道"
- `6道` → lane="6道"

### 4.4 中文引号处理

```python
_CN_QUOTE_CHARS = "\u201c\u201d\u2018\u2019\uff02\u300c\u300d"

def _strip_chinese_quotes(s: str) -> str:
    ...
```

`"蓝鳍"` 中的中文引号被剥离，然后匹配已知船名。

### 4.5 新增 5 个测试

| 测试 | 输入 | 期望 lane | 期望 ship |
|------|------|----------|----------|
| test_py_28jie_siping_lanqi | 28节四平铁，蓝鳍（26-60位） | "" | 蓝鳍 |
| test_py_mei6_siping_lanqi_18jie | 煤六   四平铁"蓝鳍"18节 | 煤六 | 蓝鳍 |
| test_py_9dao_sipingnie_changhang_46jie | 九道   四平镍"长航滨海"46节 | 九道 | 长航滨海 |
| test_py_14dao_41jie_siping_xiamenshiji | 十四道 41节 四平铁 厦门世纪 | 十四道 | 厦门世纪 |
| test_py_mei6_39jie_siping_zhihui | 煤六 39节 四平铁 智慧 | 煤六 | 智慧 |

---

## 5. 新增/修改文件

| 文件 | 类型 | 描述 |
|------|:--:|------|
| `src/ops_hub/sop/freight_detail_extractor.py` | 新增 | 货运详情文本解析器（188 行） |
| `tests/functional/test_freight_detail_extractor.py` | 新增 | 10 个测试 |
| `src/ops_hub/sop/departure_text_parser.py` | 修改 | +2 船名, +1 目的地别名, 车道匹配增强, 中文引号剥离（247 行） |
| `tests/functional/test_departure_text_parser.py` | 修改 | +5 新文本样式测试（总计 24 tests） |
| `config/project_sops/jilin_jingang.yaml` | 修改 | freight_detail_flow 扩展 + departure_flow 5 新示例 |
| `tests/functional/test_db_schema_migration_r39.py` | 修改 | 3 tests 适配 post-R39 生产 DB |

---

## 6. 测试汇总

```
pytest tests/functional -v  →  148 passed, 0 failed

新增测试数：
  test_freight_detail_extractor.py:  10 new
  test_departure_text_parser.py:      5 new
  test_db_schema_migration_r39.py:    3 fixed
  总计:                               18 new tests

回归测试：零失败。
```

---

## 7. 下一轮建议

| 轮次 | 任务 | 优先级 |
|:--:|------|:--:|
| R41 | 将 FreightDetailCandidate 集成到 task_execution_registry（register `extract_freight_detail` executor） | P1 |
| R42 | build_time_window executor — departure_text → ±60m 95306 查询窗口 | P0 |
| R43 | query_95306_waybills + create_wagon_shipments executor | P0 |
