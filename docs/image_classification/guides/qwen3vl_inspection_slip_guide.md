# Qwen3-VL 检装车通知单指南（已过时）

这份文档描述的是早期检装车通知单实验方案，已经与当前代码实现不一致。

当前不再适用的内容包括：

- `is_loaded`
- `cargo_info_effective`
- 继承补全驱动的后处理
- “先产出中间层 JSON，再转 Excel”
- 旧版输出 schema

这些内容会误导新线程回到已经废弃的设计。

当前应以这些文档为准：

- [inspection_slip.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/inspection_slip.md)
- [business_group_outputs.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/business_group_outputs.md)
- [sentinel_runtime.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/sentinel_runtime.md)
- [testing.md](/Users/qicai21/projects/repos/wx-ops-agent/docs/testing.md)

如果后续需要保留历史背景，可把这份文件只当作“旧方案记录”，不要作为当前实现依据。
