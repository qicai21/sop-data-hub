# 工单：吉林新船未登记 known_ships 导致发车文本跳过

- **日期**：2026-08-29
- **状态**：已完成
- **范围**：吉林金钢发运文本解析 / 新船配置
- **theme**：T2_文本触发

## 现象

文本 `煤一 55节 四平铁 林加尼`（`wx_2026-08_2552`）已命中四平与车数，
但未解析出船名，链路以 `ship=''` 跳过。95306 随后已出现同窗 55 票。

## 根因

`config/project_sops/jilin_jingang.yaml` 的 `project_meta.known_ships` 未登记新船“林加尼”；
解析器仅从该白名单识别吉林发车文本中的船名。

## 修复

1. 登记“林加尼”。
2. 增加 `煤一 55节 四平铁 林加尼` 回归测试。
3. 强制重跑 `wx_2026-08_2552`，以 95306 同制票窗 55 票入库并执行标准后续动作。

## 处理结果

- 解析回归：`tests/functional/test_departure_text_parser.py` 新增该文本用例，解析为
  `林加尼 / 四平 / 煤一 / 55车`；该文件共 26 项通过。
- 95306 事实：制票时间 `2026-08-29 10:53:48` 至 `10:53:54`，共 55 个 `ydid`、
  110 箱，标载合计 3520.0 吨。
- 入库：55 个 `ydid`、110 条箱级记录均归属
  `jilin_jingang_jinzhou|林加尼|铁矿|四平|2026-08-27|lot01`，订单标识
  `CGR20260828092436`。
- 收货人系统：按订单标识反查 110 条，本次 110 个 `箱号|车号` 均存在且唯一。
- 发运 Excel：已生成并发送至 `[GROUP013]`。初次跳过时创建的 fallback 外部动作已
  标为 `skipped`，正式批次幂等键下的 Excel、门户上传、微信发送均标为 `executed`。

## 验收

- 文本解析返回 `optional_ship_name=林加尼`。
- 55 个 `ydid`、110 箱归属林加尼 lot01；不混入其他四平批次。
- Excel、收货人上传及上传后反查均有审计结果。

## 验证

- `PYTHONPATH=src .venv/bin/python -m pytest tests/functional/test_departure_text_parser.py -q`
- `make test`
- `make smoke-runtime`
