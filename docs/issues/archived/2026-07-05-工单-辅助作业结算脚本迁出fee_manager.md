# 工单: 辅助作业结算脚本迁出 fee_manager

状态：已处理

## 背景

九三辅助作业审核分配表、对账单、现场确认单这条链已明确不属于 `sop-data-hub` 主体能力,更适合作为独立项目仓维护。

同时这条链需要独立 Python 依赖、模板、脚本入口和项目文档,继续挂在 `sop-data-hub` 中会放大仓库边界和维护噪音。

## 本次处理

1. 在 `repos/` 下新建独立仓：
   - `/Users/qicai21/projects/repos/fee_manager`
2. 迁入代码与测试：
   - 审核分配表 helper
   - 审核分配表 -> 对账单/现场确认单 helper
   - 九三现场确认单 PDF 渲染逻辑
   - 对应测试
3. 迁入设计文档副本到新仓：
   - `docs/业务系统与结算设计/`
4. 新仓默认输出继续写入 iCloud 档案库：
   - `_商务业务利润分配计划/业务系统与结算设计`
   - `P008_大豆/辅助作业结算资料`
5. 从 `sop-data-hub` 删除这条链对应的新旧脚本与测试。

## 迁出范围

已从 `sop-data-hub` 移除：

- `scripts/generate_jiusan_aux_docs_from_review.py`
- `scripts/generate_jiusan_onsite_confirm_receipts.py`
- `src/sop_hub/fees/settlement_review.py`
- `src/sop_hub/fees/aux_review_generation.py`
- `src/sop_hub/fees/jiusan_onsite_receipt.py`
- `tests/test_fee_settlement_review.py`
- `tests/test_aux_review_generation.py`
- `tests/test_jiusan_onsite_receipt.py`

保留在 `sop-data-hub` 的仍是数据主库和部分旧费用事实生成链,当前生成脚本默认仍从：

- `sop-data-hub/data/sop_agent.db`

读取业务数据。

## 验证

已在新仓验证：

```bash
PYTHONPATH=src python3 -m pytest -q
python3 -m py_compile src/fee_manager/review_sheet.py src/fee_manager/review_generation.py src/fee_manager/jiusan_receipt.py scripts/generate_jiusan_aux_docs_from_review.py scripts/generate_jiusan_onsite_confirm_receipts.py
PYTHONPATH=src python3 scripts/generate_jiusan_aux_docs_from_review.py
```

结果：

- `11 passed`
- `py_compile` 通过
- 对账单、现场确认单 PDF、zip 成功生成到 iCloud 项目目录
