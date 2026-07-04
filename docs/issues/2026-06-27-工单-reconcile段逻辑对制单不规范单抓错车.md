# 工单:reconcile-inspection 段逻辑在"制单不规范/非连续分票"单上抓错车(会入错车号)

- **类型**:代码缺陷(高危 —— 直接导致错车入库/结算)
- **状态**:已处理
- **处理顺序**:5
- **发现日期**:2026-06-27
- **发现会话**:数据运维(只改数据,不改代码)
- **处理归属**:系统开发会话

## 现象

中唐 检装车单 `32_b1b26f5bacab2d8a9a677f93abc8f377.jpg`(煤五,55节=53装+2排,贝拉+丰收散运两船混在一张单)。制单人把"贝拉/3节"标注只压在 seq39 一行,实际贝拉是 seq37-39 三车 `[1724096, 1529031, 1842553]`,其余 50 车(+2排)是丰收散运。

`reconcile-inspection` 对贝拉候选 commit 时,`safe_to_commit=True` 但**写入了错车**:
- 实际写:inspection_row 39/40/41 = `1842553, 1857795, 1712661`(从"3节"标注 seq39 往后抓 3 连续行)
- 正确应:seq37-39 = `1724096, 1529031, 1842553`
→ 后两车 1857795/1712661 其实是丰收的,被错记到贝拉。

丰收候选同样:段逻辑只切出 34(seq3-36 的连续段),seq40-55 的 16 丰收车被 `outside-target-release-segment` 丢弃。

## 根因

`src/sop_hub/matching/inspection_95306_reconciler.py`:
- `_ship_segment_rows` / `_segment_start` / `_nearby_section_count`(line 318-356):按"船名标注行 + 邻近 N节 + 连续匹配段"切 release 段。
- 假设**同一船的车在通知单里连续**。制单不规范时(两船交错、"N节"标注位置错位),段切分**起点/长度都会错**,且 `safe_to_commit` 仍可能为 True → 静默写错车。
- 读 `payload_json.rows`(line 135),不读人工纠正后的 `car_numbers_json` —— 人工改 car_numbers_json 不生效。

## 期望 / 建议

- 段切分不应假设连续;或在 ship 标注与 95306 窗口车数不一致时强制 `requires_manual_review`,不给 safe_to_commit。
- 允许人工指定"该候选的权威车号集"(读 car_numbers_json 或显式入参)覆盖段切分。
- "N节"标注与实际段长不符时报警。

## 数据侧已处置(本会话,绕开工具)

- 删掉 reconcile 写错的 3 行 `shipment_release_batch_matches`,按 95306 权威重写 贝拉3 + 丰收50 的匹配行。
- 用 `ingest_wagons` 把 贝拉3 / 丰收50 正确写入 `wagon_shipments`(结算源):贝拉 lot05 → 145车,丰收 lot08 → 50车,actual_wagon_count 已自愈正确。
- 两候选标 committed。
- **教训**:制单不规范的多船混合单,reconcile 不可信,须按 95306 权威 + 人工确认的车号直接 ingest_wagons。

关联 [[2026-06-27-工单-pending_freight批次未自动推进致检装车候选挂起]]、记忆 inspection_payload_multi_group_split(#130)、95306-window-reconcile-rule。

## 处理记录

2026-07-04 已完成代码加固：

- `inspection_95306_reconciler` 不再让混合检装车单的连续段猜测直接 `safe_to_commit`。
- 当 `_ship_segment_rows` 通过“船名标注 + N节”切出一段，同时候选里还存在 `outside-target-release-segment` 时，新增人工复核原因：
  - `mixed-inspection-segment-requires-authoritative-car-set`
- 支持人工/上游修正后的权威车号集：
  - 若 `inspection_ingestion_candidates.car_numbers_json` 与 `payload_json.rows` 车号列表不一致，则视为权威车号集；
  - reconcile 只按该权威车号集查 95306 并生成正式匹配；
  - 其他 payload 行仅以 `outside-authoritative-car-set` 排除，不再参与连续段猜测。
- 这样可以覆盖本工单的“贝拉 3 车实际为 seq37-39，但标注压在 seq39 导致向后抓 3 行”的风险：没有权威车号集时不自动提交；有权威车号集时按明确车号提交。

新增/调整测试：

- `tests/test_inspection_95306_reconciler.py::test_mixed_ship_candidate_only_writes_target_release_segment`
  - 从“混合单可自动提交”调整为“混合单必须人工复核”。
- `tests/test_inspection_95306_reconciler.py::test_mixed_ship_candidate_can_commit_with_authoritative_car_numbers`
  - 验证 `car_numbers_json` 权威子集可以安全提交。

验证：

- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_inspection_95306_reconciler.py -q`
- 结果：`12 passed`
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_inspection_95306_reconciler.py tests/functional/test_inspection_window_recover.py tests/functional/test_chaoyang_authoritative_candidate_cars.py tests/functional/test_pending_match_verifier_retry_before_timeout.py tests/test_pending_freight_inspection_guards.py tests/functional/test_e2e_chains_smoke.py -q`
- 结果：`32 passed`
