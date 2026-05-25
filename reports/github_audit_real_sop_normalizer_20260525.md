# GitHub Audit Summary — minimal sop normalizer

| 字段 | 内容 |
|---|---|
| Audit 日期 | 2026-05-25 |
| Branch | codex/sop-real-sop-topology-audit-20260525 |
| Current commit | 2c87ac3 |
| PR | none |

## 1. branch

`codex/sop-real-sop-topology-audit-20260525`

## 2. commit chain（最近5个）

- `f42e954` docs: audit real sop loader boundary
- `320d97f` docs: update real sop fixture order for R4 loader boundary audit
- `599b483` docs: add r3 execution report
- `6db84e7` test: add markdown loader contract
- `45694bd` docs: add real sop fixture copies

## 3. PR

none

## 4. 本次新增文件

- `reports/real_sop_normalizer_20260525.md`
- `reports/github_audit_real_sop_normalizer_20260525.md`
- `tests/functional/test_sop_normalizer.py`

## 5. 本次修改文件

- `orders/sop_real_sop_fixture_topology_order_20260525.md`
- `src/ops_hub/models/project_sop.py`

## 6. report 路径

- `reports/real_sop_normalizer_20260525.md`

## 7. order 路径

- `orders/sop_real_sop_fixture_topology_order_20260525.md`

## 8. 是否进入 feature branch

是

## 9. 是否已 merge

否

## 10. 是否属于仅 feature 可见

是

## 11. 审计结论

本轮只新增最小 normalizer contract 和审计报告，没有接 compiler，没有扩展 runtime / publisher / wx-ops-agent / rail95306-sync，没有修改数据库 schema。
