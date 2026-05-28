# R32: Propagate freight_detail text_patterns through SOP loader

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Task:** Fix text_patterns from YAML → monitoring_plan propagation

---

## 背景

R31 确认 YAML text_patterns 定义正确、Compiler 支持 text_patterns，但 parser 层丢弃数据。本轮修复三轮传递。

## 修改

| 层 | 文件 | 行 | 变更 |
|---|------|----|------|
| Model | `project_sop.py` | 17 | `RoutingRule` + `text_patterns: Optional[List[str]] = None` |
| Parser | `project_sop.py` | 328 | `load_project_sop()` 读取 `r_data["text_patterns"]` |
| Adapter | `sop_watcher.py` | 226-228 | 传递 `rule.text_patterns` 到 compiler input |

**YAML 未修改。Compiler 未修改。**

## 验证

```
YAML → RoutingRule     GROUP005: 10 patterns ✅  GROUP013: 10 patterns ✅
        → compiler_input  GROUP005: NON-EMPTY ✅  GROUP013: NON-EMPTY ✅
        → monitoring_plan GROUP005: NON-EMPTY ✅  GROUP013: NON-EMPTY ✅
        → matcher         含"合同号/标识号/货名"→ MATCH ✅  无关文本→ NO MATCH ✅
```

匹配不再使用 `message_type` 字面量，由 text_patterns 驱动。

## 测试

```
pytest tests/functional/test_freight_detail_text_patterns.py -v  → 7 passed
pytest tests/functional -v                                       → 55 passed
```

7 个新测试：parser 读取 ✓ | compiler input 传递 ✓ | monitoring_plan 含 patterns ✓ | matcher 命中 ✓ | 无关文本不命中 ✓

## 文件

- `src/ops_hub/models/project_sop.py` — RoutingRule + text_patterns；parser 读取
- `src/ops_hub/sop/sop_watcher.py` — adapter 传递 text_patterns
- `tests/functional/test_freight_detail_text_patterns.py` — 7 tests（新）

## 未处理的 P0

P0-3 departure parser, P0-4 95306 query, P0-5 write wagon_shipments, P0-6 tracking, P0-7/8 DB, P0-9 TaskResolver — 不在本轮范围。
