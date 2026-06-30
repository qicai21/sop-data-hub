# 工单:清理 `ops-data-hub` 残留路径字符串

- **类型**:文档/路径治理
- **发现日期**:2026-06-30
- **发现来源**:上手检查 `docs/project-sops/` 时发现旧项目名/旧路径残留
- **状态**:待处理
- **严重度**:中 —— 项目已统一为 `sop-data-hub`,但部分 SOP 文档仍引用旧名 `ops-data-hub`,容易误导后续开发、排查和路径定位

## 背景

用户确认:旧项目名 `ops-data-hub` 已改为 `sop-data-hub`,且项目功能已明显演进。后续代码变动、更新、调整均需进入 `docs/issues/` 工单管理并纳入 git 版本管理。

本工单只记录清理任务;尚未修改任何路径字符串。

## 现象

只读检索发现 `docs/project-sops/` 内仍有 `ops-data-hub` 残留引用,包括:

- `docs/project-sops/chaoyang_steel.md`
  - 发运报表模板路径仍写 `/Users/qicai21/projects/repos/ops-data-hub/...`
  - 缺陷词表仍写 `ops-data-hub/config/inspection_defect_terms.yaml`
  - fixture / loader 检查项仍称 `ops-data-hub`
- `docs/project-sops/zhongtang_special_steel.md`
  - 缺陷词表路径仍写 `ops-data-hub/config/inspection_defect_terms.yaml`
  - fixture / loader 检查项仍称 `ops-data-hub`
- `docs/project-sops/jiusan.md`
  - 合同文件路径仍写 `/Users/qicai21/projects/repos/ops-data-hub/...`
  - fixture / loader 检查项仍称 `ops-data-hub`

另有历史归档工单 `docs/issues/archived/2026-06-19-业务文档与仓库目录治理.md` 记录过 `ops-data-hub -> sop-data-hub` 改名背景,该类归档历史记录可保留为历史证据,不应机械改写。

## 处理范围

1. 清理活跃文档里的旧项目名和旧路径:
   - `docs/project-sops/chaoyang_steel.md`
   - `docs/project-sops/zhongtang_special_steel.md`
   - `docs/project-sops/jiusan.md`
2. 将应指向当前仓库的路径统一改为 `sop-data-hub` 下的真实路径。
3. 对不存在的旧模板/合同/配置路径逐项确认真实现状,不要只做字符串替换。
4. 保留归档工单中的历史描述,除非后续明确决定做历史文档规范化。

## 验收标准

- `rg -n "ops-data-hub" docs/project-sops config docs/00-START-HERE.md docs/business-rules` 不再命中活跃文档中的旧项目路径。
- 活跃 SOP 文档中的模板、合同、词表路径均能对应当前仓库真实文件,或明确标注为待补齐资源。
- 变更提交到 git;提交信息说明是旧名/路径治理。

## 注意事项

- 这是文档和路径治理任务,不应顺手修改业务逻辑。
- 如发现代码或配置中也存在 `ops-data-hub` 活跃引用,需扩大工单范围并补充证据后再改。
