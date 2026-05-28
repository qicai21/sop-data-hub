# R31: freight_detail_text pattern fix verification — 吉林金钢 SOP

**Date:** 2026-05-28
**Branch:** `codex/sop-real-sop-topology-audit-20260525`
**Commit:** `32c5c5b` — `docs: add freight detail text patterns to jilin jingang SOP`
**文件:** `config/project_sops/jilin_jingang.yaml`
**SOP SHA256:** `56e02e9d38ed36f8`

---

## 一、Git Log

```
32c5c5b docs: add freight detail text patterns to jilin jingang SOP
deb34f2 R30: compat版 — 吉林金钢 SOP v0.2 gap audit (YAML parser fixed)
5411674 docs: make jilin jingang SOP yaml parser compatible
4f3ea71 R30: regenerate — 吉林金钢 SOP v0.2 gap audit with YAML structure evidence
63741fb R30: add GitHub audit summary
```

---

## 二、live_service --status

```json
{
  "sop_runtime": {
    "source_of_truth": "git",
    "sop_dir": ".../config/project_sops",
    "loaded_projects": ["chaoyang_steel", "jilin_jingang_jinzhou", "jiusan", "zhongtang_special_steel"],
    "sop_hash": "a07fcb09438446d0",
    "file_hashes": {
      "jilin_jingang.yaml": "56e02e9d38ed36f8"
    }
  }
}
```

| 检查项 | 结果 |
|--------|:--:|
| `source_of_truth` = `git` | ✅ |
| `sop_dir` = `config/project_sops` | ✅ |
| `loaded_projects` 包含 `jilin_jingang_jinzhou` | ✅ |

---

## 三、YAML 检查

### GROUP005 — freight_detail_text routing

```yaml
- message_type: "text"
  trigger_condition: "freight_detail_text"
  target_node: "enrich_release_batch"
  text_patterns:
    - "标识号"
    - "订单标识"
    - "订单号"
    - "合同号"
    - "合同"
    - "货名"
    - "品名"
    - "详细货名"
    - "矿种"
    - "批次"
```

### GROUP013 — freight_detail_text routing

```yaml
- message_type: "text"
  trigger_condition: "freight_detail_text"
  target_node: "enrich_release_batch"
  text_patterns:
    - "标识号"
    - "订单标识"
    - "订单号"
    - "合同号"
    - "合同"
    - "货名"
    - "品名"
    - "详细货名"
    - "矿种"
    - "批次"
  supplemental: true
```

### YAML 路由检查结果

| 字段 | 预期 | GROUP005 | GROUP013 | 结果 |
|------|------|:--------:|:--------:|:----:|
| message_type | `text` | ✅ | ✅ | ✅ |
| trigger_condition | `freight_detail_text` | ✅ | ✅ | ✅ |
| target_node | `enrich_release_batch` | ✅ | ✅ | ✅ |
| text_patterns 非空 | ≥5 patterns | 10 patterns | 10 patterns | ✅ |
| 包含"标识号" | yes | ✅ | ✅ | ✅ |
| 包含"合同号" | yes | ✅ | ✅ | ✅ |
| 包含"货名" | yes | ✅ | ✅ | ✅ |
| 包含"品名" | yes | ✅ | ✅ | ✅ |
| 包含"订单" | yes（"订单标识"/"订单号"） | ✅ | ✅ | ✅ |

**YAML 层：正确。**

---

## 四、Compiler 链路追踪

### 4.1 RoutingRule 模型

```
dataclass RoutingRule:
  message_type: str
  trigger_condition: str
  target_node: str
  save_db: bool           ← 无 text_patterns
  send_report_to: str
  report_targets: dict
```

**`text_patterns` 字段缺失。** `load_project_sop()` 不读取 YAML 中的 `text_patterns`，解析后丢弃。

### 4.2 load_project_sop() 行为

```python
RoutingRule(
    message_type=r_data.get("message_type", "*"),
    trigger_condition=r_data.get("trigger_condition", "always"),
    target_node=r_data.get("target_node", ""),
    save_db=r_data.get("save_db", False),
    send_report_to=r_data.get("send_report_to", None),
    report_targets=r_data.get("report_targets", None)
    # ← text_patterns 未被读取
)
```

**YAML 中的 `text_patterns` 在 parser 层被丢弃。**

### 4.3 _project_sop_yaml_to_compiler_input() 行为

```python
# line 224-225
entry["input_type"] = "text"
entry["message_type"] = rule.trigger_condition or ""
# ← 未设置 entry["text_patterns"]，因为 rule 上无此属性
```

**Compiler 输入中 `text_patterns` = `None`。**

### 4.4 验证输出

```
GROUP005: text_patterns=None, input=text, msg_type=freight_detail_text
GROUP013: text_patterns=None, input=text, msg_type=freight_detail_text
```

### 4.5 Matcher 行为

```python
def _match_text_item(event_text, watch_item):
    message_type = watch_item.get("message_type")    # "freight_detail_text"
    if message_type and message_type in event_text:   # "freight_detail_text" in 真实消息? → NEVER
        return True
    for pattern in watch_item.get("text_patterns") or []:  # None → [] → 空循环
        if pattern and pattern in event_text:
            return True
    return False  # ← 永远到这里
```

**`message_type` 字面匹配永远失败（真实消息不含"freight_detail_text"），`text_patterns=None` 导致 pattern 循环为空。freight_detail_text 消息永远不匹配。**

### 4.6 结论：是 parser 缺口，非 compiler 缺口

**`monitoring_plan_compiler.py`（line 60, 75-77）正确消费 `text_patterns`。** 但 `text_patterns` 数据从未到达 compiler，因为：

1. `RoutingRule` 数据类缺少 `text_patterns` 字段
2. `load_project_sop()` 不读取 `text_patterns` 从 YAML
3. `_project_sop_yaml_to_compiler_input()` 未传递 `text_patterns` 到 compiler

**YAML 正确。Compiler 正确。缺口在 `project_sop.py` 的 `RoutingRule` 和 `load_project_sop()`。**

---

## 五、GROUP001/GROUP005/GROUP013 监控计划覆盖

```
wechat_monitoring_plan:
  GROUP001: watch_items=1  (image: 出港计划通知单 → detect_release_notice)
  GROUP005: watch_items=3  (image + text:freight_detail_text + file:departure_excel)
  GROUP013: watch_items=3  (image + text:freight_detail_text + file:departure_excel_test)
```

| Group | 进入 monitoring plan | routes | notes |
|-------|:--:|--------|-------|
| GROUP001 | ✅ | 1 image | text→match_departure_text_template 被 skip，靠 fallback |
| GROUP005 | ✅ | 1 image + 1 text + 1 file | freight_detail_text 存在但 text_patterns=None |
| GROUP013 | ✅ | 1 image + 1 text + 1 file | freight_detail_text 存在但 text_patterns=None |

---

## 六、测试

```
pytest tests/functional -v
48 passed in 2.53s
```

全绿。

---

## 七、结论

| 层级 | 状态 |
|------|:--:|
| YAML text_patterns 定义 | ✅ 正确（10 patterns per group） |
| YAML 字段映射 | ✅ 正确（message_type/trigger_condition/target_node） |
| loaded_projects | ✅ 包含 jilin_jingang_jinzhou |
| GROUP005 进入 monitoring plan | ✅ |
| GROUP013 进入 monitoring plan | ✅ |
| Compiler 消费 text_patterns | ✅ 支持（line 60, 75-77） |
| **Parser 传递 text_patterns** | **❌ 未传递 — RoutingRule 无此字段** |
| **Matcher 匹配真实消息** | **❌ 仍失败 — text_patterns=None** |

**YAML 修改正确。Compiler 已支持 text_patterns。**

**缺口在两个地方：**

1. `RoutingRule` (project_sop.py:9-17) — 添加 `text_patterns` 字段
2. `load_project_sop()` (project_sop.py:320-327) — 读取 `text_patterns` 从 YAML
3. `_project_sop_yaml_to_compiler_input()` (sop_watcher.py:224-225) — 传递 `text_patterns` 到 compiler

**下一步：改 `project_sop.py`（RoutingRule + load_project_sop），不改 SOP YAML。**

---

**审计完成。零代码变更。**
