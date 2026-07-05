# 2026-07-05 工单 - wagon_shipments 增加同列发车标识

状态：已完成

## 背景

`fee_manager` 在制作中唐 2026 年 6 月辅助作业审核分配表时发现：费用侧需要按“每一列车”生成审核分项，但当前 `sop_agent.db.wagon_shipments` 没有稳定的同列标识，只能临时根据 `ticketed_at / ydid / hph / loaded_at / departed_at` 推断列边界。

这类推断不应长期放在费用系统中。发车列是上游业务事实，应在 `sop-data-hub` 总台账库中落成稳定字段，供费用、对账、发运单、现场确认单等下游统一读取。

## 当前观察

1. `ticketed_at` 是 95306 数据中最稳定、最完整的时间字段。
   - 即使部分记录缺 `departed_at / loaded_at`，制票时间通常仍存在。
   - 例如中唐：
     - `2026-06-06` 鞍子河 52 车，首车 `1818829`，仅靠 `ticketed_at` 才能纳入 6 月发车列。
     - `2026-06-23` 贝拉 48 车，首车 `1739822`，同样需要 `ticketed_at`。
2. `ticketed_at` 也有小问题：同一列内制票时间可能有先有后，不能简单按制票日期或制票时间整段合并。
3. 同一实际列可能跨多个 `release_batch / plan_id / ship_name`。
   - 中唐样例：
     - 运单段 `202606TY4729580298-202606TY4729580350`
     - 实际为同一列：
       - 丰收散运 50 车
       - 贝拉 3 车
4. 单纯按 `loaded_at` 会错误合并或拆分。
   - 例如 `2026-06-27` 曾被费用侧误合并为丰收散运 103 车。

## 需求

在 `wagon_shipments` 中增加一个“同列发车标识”字段，用于标识同一实际发车列。

建议字段：

- `dispatch_train_code TEXT`

编码规则：

- 格式：`gqz + 日期(yymmdd) + 序号(1-9, a-z)`
- 示例：
  - `gqz2606061`
  - `gqz2606062`
  - `gqz2606271`
  - `gqz2606272`
  - 当同日超过 9 列时继续使用 `a-z`

日期口径建议：

- 优先使用实际发车/作业日期。
- 若 `departed_at / loaded_at` 缺失，则使用 `ticketed_at` 日期。
- 同一列跨多个 `release_batch / ship_name` 时，必须共用同一个 `dispatch_train_code`。

## 实现建议

1. 新增 schema 迁移：
   - `wagon_shipments.dispatch_train_code TEXT`
   - 建议增加索引：`idx_wagon_shipments_dispatch_train_code`
2. 在 95306 入库 / 回填链路中生成或回填 `dispatch_train_code`。
3. 列边界识别建议优先参考：
   - `ticketed_at` 日期窗口
   - `ydid` 连续段
   - `hph` 号段连续性
   - 已有发车文本/检装车上下文，如 `source_message_id`
4. 对历史数据增加可重跑回填脚本，至少先覆盖中唐 2026 年 6 月样例。
5. 下游 `fee_manager` 后续应改为直接读取 `dispatch_train_code`，不再自行推断列边界。

## 验收标准

1. `wagon_shipments` 存在 `dispatch_train_code` 字段和索引。
2. 中唐 2026 年 6 月以下样例可被稳定识别：
   - `2026-06-06` 鞍子河 52 车，首车 `1818829`
   - `2026-06-23` 贝拉 48 车，首车 `1739822`
   - `2026-06-27` 同一列包含丰收散运 50 车 + 贝拉 3 车，共用同一个 `dispatch_train_code`
   - `2026-06-28` 丰收散运 60 车为下一列，使用不同 `dispatch_train_code`
3. 增加功能测试：
   - `departed_at / loaded_at` 缺失但 `ticketed_at` 存在时仍能分配同列标识。
   - 同一列跨多个 `release_batch / ship_name` 时不被拆成不同列码。
   - 同日多列时按 `1-9, a-z` 顺序生成不同列码。
4. 不影响现有发运、Excel、上传链路；如有服务进程读取 schema，需要重启并确认服务状态。

## 下游影响

- `fee_manager` 当前临时用 `ticketed_at / ydid / hph` 推断列边界。
- 本工单落地后，`fee_manager` 应同步调整为读取 `dispatch_train_code`。
- 费用审核分配表、对账单、现场确认单将以该字段作为最小列级结算单元锚点。

## 处理记录

2026-07-05：

1. 新增 `src/sop_hub/sop/dispatch_train_code.py`，统一负责同列发车标识：
   - 优先按 `source_message_id` 合并同一实际发车列，允许跨 `release_batch / ship_name`。
   - 无来源消息的历史行保守回落到 `project_id + 日期 + batch_id + 道线 + 到站`。
   - 编码格式按项目短码 + 日期 + 当日列序号，例如 `gqz2606271`。
2. `wagon_shipments` 与 `wagon_container_shipments` 均新增 `dispatch_train_code` 字段和索引。
3. 新发运入库链路自动补码：
   - `wagon_ingest.ingest_wagons`
   - `create_wagon_shipments_from_candidates`
4. 新增历史回填脚本：
   - `scripts/backfill_dispatch_train_code.py`
5. 已对真实库执行回填：
   - `wagon_shipments`: 12137 行，缺失 0 行
   - `wagon_container_shipments`: 8250 行，缺失 0 行
6. 验收样例：
   - `2026-06-06` 鞍子河 52 车：`gqz2606061`
   - `2026-06-23` 贝拉 48 车：`gqz2606231`
   - `2026-06-27` 丰收散运 50 车 + 贝拉 3 车：共用 `gqz2606271`
   - `2026-06-28` 丰收散运 60 车：`gqz2606281`
   - `2026-06-28` 丰收散运 55 车：`gqz2606282`
7. 测试：
   - `.venv/bin/python -m pytest tests/test_dispatch_train_code.py tests/functional/test_wagon_ingest.py tests/test_r82_contract_fee_schema.py tests/functional/test_e2e_jilin_lanqi_50cars.py -q`
   - 12 passed
