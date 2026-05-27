# R26: Live SOP Runtime Compiler

**Date:** 2026-05-27
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Part A: SOP Source Audit

4 项 SOP 的源、fixture、runtime 路径：

| # | 项目 | Canonical Runtime源 | Git-tracked? | Fixture (test) |
|---|------|---------------------|--------------|----------------|
| 1 | 中唐 | `business-system-docs/.../zt_steel_baseline.yaml` | **否** (business-system-docs非Git) | `tests/fixtures/sops/zhongtang_special_steel_sop.md` |
| 2 | 朝阳 | `business-system-docs/.../chaoyang_steel_baseline.yaml` | **否** | `tests/fixtures/sops/chaoyang_steel_sop.md` |
| 3 | 吉林金钢 | `business-system-docs/.../jilin_jingang_jinzhou_baseline.yaml` | **否** | `tests/fixtures/sops/jilin_jingang_sop.md` |
| 4 | 九三 | `business-system-docs/.../jiusan_soybean_baseline.yaml` | **否** | `tests/fixtures/sops/jiusan_soybean_sop.md` |

### 发现

1. **Canonical 源不在 Git 中** — `business-system-docs/` 不是 git 仓库，SOP 修改无版本历史
2. **Mirror 存在** — `prompts_and_reports/test-plan/fixtures/project_sops/` 有相同内容的副本，在 git 中
3. **Test fixtures 是 .md 格式** — `tests/fixtures/sops/*.md`，与 runtime .yaml 不同格式但内容等价
4. **代码路径解析**：
   - `config.py` (parents[3]): ✓ → `business-system-docs/...`
   - `runner.py` (parents[3]): ✓ → `business-system-docs/...`
   - `agent.py` (parents[4]): ✓ → `business-system-docs/...`
   - `monitoring_plan_preview.py`: 参数驱动，无硬编码

### 当前运行时使用位置

```
run_live_service.py --fixture-dir → tests/fixtures/sops/ (默认)
                                    → business-system-docs/.../project_sops/ (生产应改为)
```

## Part B: SOPWatcher

新增 `src/ops_hub/sop/sop_watcher.py`

### 架构

```
SOP 文件 mtime 变化
  ↓
SopWatcher.check_and_reload()
  ↓ SopRuntime.compute_dir_hash() — 检测变化
  ↓ SopRuntime.reload()
  ↓   load_normalized_project_sops() — Normalizer
  ↓   normalized_project_sops_to_compiler_input() — ChainSpec
  ↓   SopMonitoringPlanCompiler().compile() — MonitoringPlan
  ↓
  ↓ 原子替换 SopRuntime.plan
  ↓ 记录: sop_hash, loaded_projects, last_reload, file_hashes
  ↓
后续 poll 使用新 plan（无需重启）
```

### 关键实现

- `SopRuntime`: 持有 hot-swappable 的 `plan`、`normalized_projects`、`sop_hash`
- `SopWatcher`: 监听 mtime，检测变化后调用 `reload()`
- 同时支持 `.yaml` 和 `.md` 格式
- 检测文件删除
- 缓存按 `fixture_dir` 绝对路径 keyed

## Part C: --status 增强

```json
{
  "pid": null,
  "alive": false,
  "runtime_root": "...",
  "chat_records_root": "...",
  "last_log_line": "...",
  "sop_runtime": {
    "loaded_projects": ["chaoyang_steel", "jilin_jingang_jinzhou", "jiusan", "zhongtang_special_steel"],
    "last_reload": "2026-05-27T09:01:36Z",
    "sop_hash": "9b31bb6e852611b5",
    "sop_dir": "/Users/qicai21/projects/repos/sop-data-hub/tests/fixtures/sops",
    "file_hashes": {
      "chaoyang_steel_sop.md": "057f52f90bbb92bb",
      "jilin_jingang_sop.md": "b5fd76c7fa2a9441",
      "jiusan_soybean_sop.md": "fce50b86f1475b08",
      "zhongtang_special_steel_sop.md": "b7c9d36815762668"
    }
  }
}
```

## Part D: Tests

新增 4 个测试，12→12 (全部 pass)：

| Test | 验证 |
|------|------|
| `test_status_includes_sop_runtime` | --status 输出包含 sop_runtime 段 |
| `test_sop_watcher_hot_reload_detects_mtime_change` | 修改文件后 mtime 检测 + hash 变化 |
| `test_sop_change_reflected_in_next_poll_without_restart` | 修改 SOP 后下一次 poll 使用新 plan |
| `test_sop_watcher_status_reflects_file_hashes` | per-file hash 完整 |

### 热重载验证细节

```
1. 复制 test fixtures → tmp_path
2. 发送消息 wx_101 (原始 SOP)
3. 修改 jilin_jingang_sop.md (追加 WAIT_DELIVERED 节点)
4. 发送消息 wx_102 (无重启)
5. 确认 wx_102 被处理 + sop_watcher 日志输出 "plan updated"
```

## Modified Files

| File | Change |
|------|--------|
| `src/ops_hub/sop/sop_watcher.py` | **新建** — SOPWatcher + SopRuntime |
| `scripts/run_live_service.py` | 集成 SOPWatcher、增强 --status、sop_runtime 字段 |
| `tests/functional/test_live_service_bootstrap.py` | +4 R26 tests |
| `tests/functional/test_db_migration_cleanup_r21.py` | 跳过 R22 report（pre-existing） |

**Zero business logic changes. No DB schema changes. No wx-ops-agent modifications.**
