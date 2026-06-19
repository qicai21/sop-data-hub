# Qwen3-VL Agent Runbook（已过时）

这份文档对应的是旧版检装车通知单实验链路。

其中包含的以下设计已经不再适用：

- 导出 Excel / xlsx
- 基于继承补全的装货信息规则
- `cargo_info_effective`
- `is_loaded`
- 旧版检装车单 schema

新线程不要再按这份文档继续开发。

请改看以下当前文档：

- [inspection_slip.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/inspection_slip.md)
- [business_group_outputs.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/business_group_outputs.md)
- [image_resolution.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/image_resolution.md)
- [testing.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/testing.md)

当前系统的真实状态是：

1. 检装车通知单只输出 JSON
2. 不再生成或发送 xlsx
3. `shallow/full` 控制是否进入深度 OCR
4. 主 schema 只保留当前实现实际使用的字段
5. 主图片定位链路是 `message key -> hardlink.db -> full_path`
