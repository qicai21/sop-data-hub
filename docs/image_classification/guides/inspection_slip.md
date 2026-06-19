# 检装车通知单处理说明

这份文档描述 `检装车通知单` 当前的分类、浅深模式、JSON 输出和字段规则。

核心实现：

- [inspection_slip.py](/Users/qicai21/projects/repos/wx-ops-agent/src/wechat_ops_agent/ocr/inspection_slip.py)
- [processors.py](/Users/qicai21/projects/repos/wx-ops-agent/src/wechat_ops_agent/ocr/business_group/processors.py)
- [ocr_logger.py](/Users/qicai21/projects/repos/wx-ops-agent/src/wechat_ops_agent/tracking/handlers/ocr_logger.py)

## 1. 当前原则

检装车通知单现在只输出 JSON。

已经移除的旧能力：

- xlsx 生成
- xlsx 发送
- 固定中间产物目录

当前所有产物都围绕：

- 图片保存
- JSON 落盘
- 必要摘要通知

## 2. `doc_detail_mode`

支持两档：

- `shallow`
- `full`

开关定义在 [doc_detail_mode.py](/Users/qicai21/projects/repos/wx-ops-agent/src/wechat_ops_agent/ocr/doc_detail_mode.py)。

### 2.1 `shallow`

`shallow` 下：

- 保留分类结果
- 保存图片
- 写基础 JSON
- 不调用 `InspectionSlipEngine.process_image(...)`

基础 JSON 至少包含：

- `doc_type`
- `detail_mode`
- `detail_skipped`
- `image_path`
- `relative_image_path`
- `group_name`
- `group_wxid`
- `message_time`
- `classification`

### 2.2 `full`

`full` 下：

- 进入 `InspectionSlipEngine`
- 产出完整结构化 JSON
- 保留 meta / footer / rows / rows_count

## 3. 行字段规则

当前检装车单 OCR 的核心原则是：

- 保留逐行原始识别
- 不做“继承补全”字段

### 3.1 当前核心字段

普通检装车单行字段以这些为核心：

- `seq`
- `car_type`
- `car_no`
- `cargo_info_raw`
- `remark`

### 3.2 明确不再做的字段

以下历史字段不再保留为当前主 schema：

- `cargo_info_effective`
- `is_loaded`

这类基于上下文继承和补全的字段已经去掉，避免下游误以为它们是“确定值”。

### 3.3 仍可能出现的附加字段

当前代码里，以下字段仍可能出现：

- `defect`
  - 普通单据和氧化铝单据都可能有
- `tarp_no`
- `piece_count`
  - 主要见于氧化铝单据

因此可以把 schema 理解为：

- 核心字段固定
- 少量业务附加字段按版式出现
- 不再做继承补全推断字段

## 4. full 模式 JSON 结构

`full` 模式下，常见顶层字段包括：

- `is_inspection`
- `doc_type`
- `title`
- `meta`
- `footer`
- `rows`
- `rows_count`
- `car_nos`
- `preview_path`
- `message`

其中：

- `meta` 常见字段：
  - `daoxian`
  - `jieshu`
  - `date`
  - `jiancheyuan`
- `rows` 是逐行 OCR 结果
- `car_nos` 是 processor 从 `rows` 汇总出的唯一车号列表

## 5. 落盘位置

检装车通知单 JSON 默认写到：

- `data/loaded_cars/<群名>/检装车通知单/`

文件名格式：

- `YYMMDD-seq-data.json`

同时会追加：

- `records.jsonl`

实现见 [stores.py](/Users/qicai21/projects/repos/wx-ops-agent/src/wechat_ops_agent/ocr/business_group/stores.py)。

## 6. 兼容入口

除了 `business_image_router -> pipeline -> processor` 这条主链，旧的 `OcrLoggerHandler` 也仍然可以直连检装车通知单处理。

但这条旧入口现在也已经统一使用：

- `InspectionSlipProcessor`
- `doc_detail_mode`

所以不应再出现“一条链 shallow，另一条链 full”的分叉行为。

## 7. 相关测试

修改检装车单逻辑前，至少更新这些测试：

- `tests/unit/test_inspection_slip_processor.py`
- `tests/unit/test_ocr_logger_manual_mode.py`
- `tests/unit/test_business_group_pipeline.py`

如果改到引擎内部 schema，还应补 engine 级测试。
