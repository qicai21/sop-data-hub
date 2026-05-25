# Report: SOP Data Hub R1-2 Test Hygiene Fix

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_data_hub_runtime_order_20260525.md |
| 执行日期 | 2026-05-25 |
| 执行者 | local Hermes |

## 1. 结论
R1-1 bootstrap 已完成。本轮只修正路径、报告、测试卫生，不实现 `SopMonitoringPlanCompiler.compile()`。

## 2. 本轮修正
- 将本地工作路径纠正为 `~/projects/repos/sop-data-hub`。
- 更新 R1-1 报告中的旧路径引用。
- 取消 `test_sop_monitoring_plan_compiler_import_contract_exists()` 的 `xfail`，保留 import contract 断言。
- 新增本轮报告文件。

## 3. R1-1 报告文件状态
- `reports/local_sop_data_hub_branch_bootstrap_20260525.md`：更新，修正路径引用。
- `reports/github_audit_local_sop_data_hub_branch_bootstrap_20260525.md`：更新，补充路径迁移说明。

## 4. XPASS 根因
`test_sop_monitoring_plan_compiler_import_contract_exists()` 原先标记为 `xfail`，但 `SopMonitoringPlanCompiler` 类已存在且可导入，因此测试在不实现 `compile()` 的前提下通过，导致 `XPASS`。

## 5. 变更文件
- `reports/local_sop_data_hub_branch_bootstrap_20260525.md`
- `reports/github_audit_local_sop_data_hub_branch_bootstrap_20260525.md`
- `reports/test_hygiene_fix_20260525.md`
- `tests/functional/test_sop_monitoring_plan_compiler.py`

## 6. 测试命令与结果
- `pytest tests/functional/test_sop_monitoring_plan_compiler.py -v` -> 1 failed, 1 passed
- `pytest tests/functional/test_sop_data_hub_source_supervision.py -v` -> 1 skipped
- `pytest tests/functional -v` -> 1 failed, 1 passed, 1 skipped

## 7. 失败与跳过说明
- 失败：`SopMonitoringPlanCompiler.compile()` 仍然抛 `NotImplementedError`。
- 跳过：`test_sop_data_hub_source_supervision.py` 仍保持 `skip`，因为跨模块监督适配器尚未设计。

## 8. 确认项
- `compile()` 未实现。
- 本轮未修改 `wx-ops-agent`。
- 本轮未修改 `rail95306-sync`。
- 本轮未修改数据库 schema。

## 9. 下一步建议
`R2: Implement SopMonitoringPlanCompiler.compile() to satisfy the functional test.`
