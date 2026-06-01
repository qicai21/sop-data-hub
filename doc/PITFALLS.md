# sop-data-hub 开发踩坑指南

> 给后续 agent 的备忘录 — 别重复踩这些坑。

---

## 1. batch_key 必须包含时间维度

**坑**：`batch_key` 曾经只用 `ship|cargo|consignor|consignee|dest|seq`，不含任何日期。
同一条船的不同通知单（不同日期）会生成相同的 key → `ON CONFLICT(batch_key)` 直接覆盖旧数据。

**正确做法**（commit `a6a8982`）：
```
batch_key = ship | cargo | dest | remark_date | sequence
```
- `remark_date`：优先取 remark 中的日期（`normalize_chinese_date(dt)`），fallback 到 `notice_date`
- `consignor`/`consignee` **不要放进 batch_key** — OCR 识别不稳定，港口出单时也会打错字

**相关代码**：[agent.py L1286-1307](../src/ops_hub/data_agent/agent.py)

---

## 2. 去重有三层，都要对齐

放货批次 ingestion 有三道去重防线，改一道必须检查另外两道是否一致：

| 层级 | 位置 | 逻辑 |
|------|------|------|
| **第一层**: sequence dedup | `normalize_release_batch_payloads()` L1222-1282 | 查 DB 已有 batch，按 `seq + batch_date + batch_quantity` 比对，跳过已存在的 remark |
| **第二层**: batch_key UNIQUE | `normalize_release_batch_payloads()` L1301 | 生成 batch_key，id = `hash_text(batch_key)` |
| **第三层**: ON CONFLICT | `ingest_release_batch()` L451 | SQL `ON CONFLICT(batch_key) DO UPDATE SET ...` |

**注意**：第一层查询用 `_query_existing_batches_like(ship, cargo, dest)` — 不含日期。
这是**正确的**：目的是找出同船所有已有 lot 来做逐条比对。
日期区分在第二层 batch_key 中体现。

---

## 3. `_move_processed_artifacts` 真的要 move

**坑**：函数名叫 `_move_processed_artifacts`，但之前用的是 `shutil.copy2` — 导致图片永远不会从原目录移走，产生多个副本。

**正确做法**（commit `a6a8982`）：
- 用 `shutil.move`
- 处理完清理 `_vlm.jpg`（VLM 分类缩略图，classifier/engines 在原图同目录创建）

---

## 4. `_vlm.jpg` 预览文件的生命周期

classifier 和每个 engine 都有独立的 `_prepare_preview()` 方法，在原图同目录创建 `{stem}_vlm.jpg`。

**5 个地方**都有相同实现（未统一到 `utils/image_utils.py`）：
- `classifier/classifier.py`
- `engines/departure_plan.py`
- `engines/handwritten_list.py`
- `engines/inspection_slip.py`
- `engines/materials_stats.py`

有 `if preview_path.exists(): return` 守卫，所以多次调用不会重复写，但文件会一直留在原图目录。
现在由 `_move_processed_artifacts()` 负责清理。

---

## 5. source_watcher 中同一 message_id 可能出现多次

**坑**：`replay-one` 模式找到第一个匹配的 message_id 就返回，但同一个 `wx_2014` 可能同时出现在 `2026-04.jsonl`（waiting_media）和 `2026-05.jsonl`（ready）中。

**原因**：微信消息被记录了两次（跨月份 JSONL），一次是占位（图片未下载），一次是完整版。

**正确行为**：正常 poll 模式中 cursor 会跳过旧的 April 条目，只处理 May 的 ready 版本。
但 `replay-one` 没有 cursor，会命中第一个（April 的 waiting_media 版本）→ 早退。

---

## 6. `classified_output_dir` 和 `wechat_images_dir` 指向同一目录

`config/settings.yaml` 中两个路径都指向 `~/Documents/bussiness-artifacts/wechat_images`。

这意味着 project archive 是 wechat_images 的子目录 (`business/projects/...`)。
不要假设它们是独立的目录树。

---

## 7. `normalize_chinese_date` 处理多种格式

输入可能是：
- `"2026年05月11日"` → `"2026-05-11"`
- `"5月21日"` → 当前年推断 → `"2026-05-21"`
- `"2026-05-11"` → 原样返回
- `None` → `None`

在 batch_key 中使用日期时**必须先 normalize**，否则同一天的不同格式会生成不同的 key。

---

## 8. remarks 为空 ≠ 无放货

某些通知单（如宝腾海）remarks 只有一条空 remark `{"date":"","sequence":"","plan":"","raw_line":""}`。
这代表**全船放货**：

- `synthesize_missing_clean_bottom_remarks()` 不会填充（因为已有 1 条 remark）
- L1177-1182 会给它赋 `sequence = "lot01"`（单条无 seq 的 remark 默认 lot01）
- 货量从 `special_matter` 或 `cargo_info.总重里` 提取

**千万别**因为 remark.date 为空就跳过这种记录。`batch_date_key` 会正确 fallback 到 `notice_date`。

---

## 9. 测试 baseline 有 pre-existing failures

截至 commit `a6a8982`，以下测试是已知失败，**不是你的改动引起的**：

- `test_dispatch_board.py` — 13 个（dispatch board 重构相关）
- `test_runner_artifacts.py` — 5 个（路径 `business/projects/` vs `projects/` 断言不一致）
- `test_image_ingestion_contract.py::test_lobster_sandbox_*` — 1 个（extraction_saved_path 指向 `.`）
- `test_live_service_bootstrap.py` — 4 个（live service 启动相关）

修改代码后跑测试时，**先跑 baseline 确认已有失败数**，再跑你的改动版本对比。方法：
```bash
git stash && pytest tests/ --tb=no -q | tail -5
git stash pop && pytest tests/ --tb=no -q | tail -5
# 对比 failed 数，差值 = 你引入的回归
```
