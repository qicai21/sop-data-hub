# 工单：更新 onboarding 入口与 fee_manager 联动边界

- **日期**：2026-08-08
- **状态**：已完成
- **范围**：`docs/00-START-HERE.md`、`README.md`；不改业务数据、不改费用库
- **theme**：文档治理 / 跨仓事实边界

## 现象

`00-START-HERE.md` 停留在 2026-06-29，未覆盖 status-sync、Web 看板、九三循环追踪、候选链路和费用迁移。README 仍把 tmux 看板当生产入口、把 daemon 数量写成旧口径。

费用已迁到 sibling `fee_manager`，但 SOP 与费用库间涉及放货批次、到厂重量和发运归属的单向同步契约没有在 onboarding 中明确，容易造成跨库反向写或只在费用库修事实。

## 修复

- 更新 onboarding：现行 daemon、Web/CLI 看板分工、候选链路、顺序 lot 匹配、九三子系统、挂起诊断入口和工单归档纪律。
- 明确 `sop-data-hub -> fee_manager` 的运输事实单向同步：`release_batches`（含 `cargo_arrival_weight`）、批次匹配和两张 wagon 表由 SOP 维护；费用、结算计划、合同费目和单证由 fee_manager 维护。
- 规定涉及批次口径、到厂重量、运单/箱级归属的变更，必须在 SOP 修正事实后执行费用侧 source sync，并在两个仓分别建立对应工单和回归验证；禁止从 fee_manager 反写 SOP。
- 同步 README 的快速入口，避免与 onboarding 漂移。

## 验收

- `docs/00-START-HERE.md` 保持 onboarding 唯一入口，正文只保留心智模型、入口、铁律与代码地图。
- README 与 launchd 配置一致；不再把 tmux 写成唯一生产看板。
- 文档检查和测试收集命令可执行。

## 结案

- 已更新 `docs/00-START-HERE.md` 与 README，覆盖现行服务、Web 看板、候选链路、九三循环、顺序 lot 匹配、状态语义及费用单向同步。
- 已核实 fee_manager 的 source sync 实际同步 `release_batches`（含 `cargo_arrival_weight`）、批次匹配关系和两张 wagon 表；费用本地表不参与覆盖。
- 验证：`git diff --check`；`PYTHONPATH=src .venv/bin/python -m pytest --collect-only -q`（667 collected）；`PYTHONPATH=src .venv/bin/python -m pytest -q`（667 passed）。
- `scripts/check_issues_hygiene.sh` 仍报告既有历史工单已完成但留在根目录的治理债；本单未移动无关历史工单。
