# Report: Real SOP Monitoring Plan Preview

| 字段 | 内容 |
|------|------|
| Order ID | orders/sop_real_sop_fixture_topology_order_20260525.md |
| Round | R6 |
| 执行日期 | 2026-05-25 |
| 仓库 | qicai21/ops-data-hub |
| 分支 | codex/sop-real-sop-topology-audit-20260525 |
| 当前目录 | /Users/qicai21/projects/repos/sop-data-hub |
| 当前 commit | bd7aab8 |
| Python version | Python 3.11.15 |

## 1. 结论

R6 完成了真实 4 个 SOP fixture 的链路预览：`Normalizer -> Compiler -> wechat_monitoring_plan`。

这不是 runtime，也不是实际微信联动；只是把现有的真实 SOP 结构化输入转换成编译器可消费的项目 SOP 形状，并输出可读的监控计划预览。

## 2. 输入 fixture 列表

- `tests/fixtures/sops/chaoyang_steel_sop.md` → `chaoyang_steel` / 朝阳钢铁铁矿发运项目
  - title: 朝阳钢铁铁矿发运项目 SOP
  - groups: GROUP001
  - monitoring entries: 3
- `tests/fixtures/sops/jilin_jingang_sop.md` → `jilin_jingang_jinzhou` / 吉林金钢-锦州港铁矿发运项目
  - title: 吉林金钢-锦州港铁矿发运项目 SOP
  - groups: GROUP001
  - monitoring entries: 5
- `tests/fixtures/sops/jiusan_soybean_sop.md` → `jiusan` / 九三大豆铁路发运项目
  - title: 九三大豆铁路发运项目 SOP
  - groups: GROUP013
  - monitoring entries: 4
- `tests/fixtures/sops/zhongtang_special_steel_sop.md` → `zhongtang_special_steel` / 中唐特钢铁矿发运项目
  - title: 中唐特钢铁矿发运项目 SOP
  - groups: GROUP001, GROUP003
  - monitoring entries: 4

## 3. 归一化项目数

- 4

## 4. Generated WeChat monitoring plan

# Real SOP Monitoring Plan Preview

## 1. Input fixtures
- `chaoyang_steel_sop.md` → `chaoyang_steel` / 朝阳钢铁铁矿发运项目
  - title: 朝阳钢铁铁矿发运项目 SOP
  - groups: GROUP001
- `jilin_jingang_sop.md` → `jilin_jingang_jinzhou` / 吉林金钢-锦州港铁矿发运项目
  - title: 吉林金钢-锦州港铁矿发运项目 SOP
  - groups: GROUP001
- `jiusan_soybean_sop.md` → `jiusan` / 九三大豆铁路发运项目
  - title: 九三大豆铁路发运项目 SOP
  - groups: GROUP013
- `zhongtang_special_steel_sop.md` → `zhongtang_special_steel` / 中唐特钢铁矿发运项目
  - title: 中唐特钢铁矿发运项目 SOP
  - groups: GROUP001, GROUP003

## 2. Normalized project count
- 4

## 3. Generated wechat_monitoring_plan
### Group `GROUP001`
- group name: 铁晟业务工作群
- watch item 1
  - input_type: document
  - document_type: 出港计划通知单
  - candidate_projects: chaoyang_steel, jilin_jingang_jinzhou, zhongtang_special_steel
  - target_sop_nodes: chaoyang_steel: chaoyang_steel:GROUP001:2_铁晟业务工作群___GROUP001; jilin_jingang_jinzhou: jilin_jingang_jinzhou:GROUP001:2_铁晟业务工作群___GROUP001; zhongtang_special_steel: zhongtang_special_steel:GROUP001:3_铁晟业务工作群___GROUP001
- watch item 2
  - input_type: document
  - document_type: 检装车通知单
  - candidate_projects: chaoyang_steel, zhongtang_special_steel
  - target_sop_nodes: chaoyang_steel: chaoyang_steel:GROUP001:2_铁晟业务工作群___GROUP001; zhongtang_special_steel: zhongtang_special_steel:GROUP001:3_铁晟业务工作群___GROUP001
- watch item 3
  - input_type: document
  - document_type: 手写箱号表
  - candidate_projects: jilin_jingang_jinzhou
  - target_sop_nodes: jilin_jingang_jinzhou: jilin_jingang_jinzhou:GROUP001:2_铁晟业务工作群___GROUP001
- watch item 4
  - input_type: text
  - message_type: 文字报告
  - text_patterns: 文字报告
  - candidate_projects: jilin_jingang_jinzhou
  - target_sop_nodes: jilin_jingang_jinzhou: jilin_jingang_jinzhou:GROUP001:2_铁晟业务工作群___GROUP001
### Group `GROUP003`
- group name: 中唐特钢发运群
- watch item 1
  - input_type: document
  - document_type: 出港计划通知单
  - candidate_projects: zhongtang_special_steel
  - target_sop_nodes: zhongtang_special_steel: zhongtang_special_steel:GROUP003:2_中唐特钢发运群___GROUP003
- watch item 2
  - input_type: text
  - message_type: 文字放货信息
  - text_patterns: 文字放货信息
  - candidate_projects: zhongtang_special_steel
  - target_sop_nodes: zhongtang_special_steel: zhongtang_special_steel:GROUP003:2_中唐特钢发运群___GROUP003
### Group `GROUP013`
- group name: 数据单发群
- watch item 1
  - input_type: text
  - message_type: 文字
  - text_patterns: 文字
  - candidate_projects: jiusan
  - target_sop_nodes: jiusan: jiusan:GROUP013:3_数据单发群___GROUP013

## 4. Limitations / warnings
- This is a preview only; it does not call WeChat, 95306, runtime, publisher, or DB.
- Entries without a WeChat group token are skipped by the adapter.
- The preview uses extracted document/message keywords only; it does not add new business inference.

## 5. Usability check
- The generated plan is readable and sufficient for a next-round review, but it is not production-ready.


## 5. 限制 / 警告

- 只做 preview，不调用 WeChat、95306、runtime、publisher 或数据库。
- group token 缺失的监控入口会被 adapter 跳过。
- 这里只使用 normalizer 已抽取的 document/message keywords，不新增业务推断。
- 这份 plan 适合下一轮审阅，不是生产配置。

## 6. 测试与验证

### 测试命令

```bash
pytest tests/functional/test_real_sop_monitoring_plan_preview.py -v
pytest tests/functional -v
```

### 测试结果

- `pytest tests/functional/test_real_sop_monitoring_plan_preview.py -v` -> 2 passed
- `pytest tests/functional -v` -> 13 passed, 1 skipped

## 7. 改动文件

- `src/ops_hub/sop/monitoring_plan_preview.py`
- `tests/functional/test_real_sop_monitoring_plan_preview.py`
- `reports/real_sop_monitoring_plan_preview_20260525.md`
- `reports/github_audit_real_sop_monitoring_plan_preview_20260525.md`

## 8. Git

- branch: codex/sop-real-sop-topology-audit-20260525
- commit: bd7aab8

## 9. 下一步建议

- 如果继续，只调整 adapter 的输入映射或预览格式；不要进入 runtime、publisher 或真实微信接入。
