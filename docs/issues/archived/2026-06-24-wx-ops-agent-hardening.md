# wx-ops-agent 加固:丢图根因 + 数据安全/稳定红线

- **提出日期**:2026-06-24(全面只读分析后,3 个 Explore agent 分维度 + 人工核实关键论断)
- **类型**:健壮性 / 数据安全 / bug 根治
- **背景**:图片消息常卡 `media_status='waiting_media'`(检装车通知单图片不下来 → 不分类 → 不生成候选 → 业务链路断)。今天复发两次(wx_1637 / wx_1663)。借此**全面检查 wx-ops-agent**(基本没动过的模块),发现的不止丢图,还有数据安全/稳定红线。
- **吸收**:本工单吸收并取代 `2026-06-23-wx-media-download-retry.md`。

## 维度分析(✓=读原码核实)
- **捕捉**:60s 轮询微信库,create_time 游标,shadow DB + SQLCipher。
- **存储**:JSONL 逐群逐月(seq 单月连续、跨月不全局唯一);媒体 image_todo + wechat_images。
- **挂起/媒体**:微信原图**懒加载**(不点大图不下、过期清缓存),agent 只能查找+UI 诱导下载,不能强制。
- **UI**:wx-ui-bridge/primitives/wechat.py 大量 sleep(多为 #146 事故后的必要缓冲)。
- **稳定性**:主循环无异常隔离、无单实例锁。

## 待办(除"改不动"外全改)
- **① 游标在存储失败时仍推进 → 丢消息** ✓〔必改·数据安全〕
  `daemon.py:641-665`:`append_records` 在 try 内,但 `cursor_mgr.advance` 在 try **外** → 存储抛异常只打 warning、游标照推 → 消息永久丢。改:存储成功才推进游标。
- **② image_todo pending 只算"今天" → 跨日图弃疗(丢图根因)** ✓〔必改·bug 根因〕
  `daemon.py:~1013` `if not record_time.startswith(today): continue` → 昨天没下来的图今天起不再拉。改:计 pending 看最近 N 天(未 resolved 的)。
- **③ 主循环无 try/except → 崩 daemon** ✓〔必改·稳定〕
  `daemon.py:run()` 1108-1118:`_run_monitor_cycle` 未捕获异常直接崩。改:包 try/except,记错继续。
- **④ 无单实例锁 → 并发写 ledger/cursor/todo 损坏** ✓〔必改·稳定,与历史"daemon 自锁污染"同源〕
  改:启动取 `data/daemon.lock` 文件锁,已被占用则拒启。
- **⑤ image_todo 只 append、无 mark_resolved/清理(卫生债)** ✓〔应做,且 ② 依赖它〕
  图下来后旧"未下载"记录永留 → 配合 ② 必须能标记 resolved,否则 pending 计数虚高。
- **⑥ UI sleep 收紧** 〔可优化〕
  wechat.py:滚动 1.0→0.8、搜索 1.2→1.0、滚轮 0.02→0.01、最小化 0.3→0.15。**保留** #146 必要缓冲(select 0.6/input 0.25/粘贴 0.15/发文件 1.6/发送 0.5)。
- **改不动(约束)**:微信原图懒加载 + 旧消息滚出 UI 可达范围。只能趁图新+可达时积极拉,拉不到接受降级,做不到 100%。
- **边界**:真正显示 `waiting_media` 的是下游 sop-data-hub。wx-ops-agent 把图下来后,**sop-data-hub 是否重新 ingest** 是另一半,需单独核(完整修复跨两仓)。

## 顺序
①③④(数据/稳定红线,contained)→ ⑤+②(媒体核心,需读 resolution 流)→ ⑥(wx-ui-bridge,另一仓)→ 边界(下游 re-ingest)。

---
## 进展(2026-06-24)

### ✅ 已做(commit wx-ops-agent `776b5f1`)
- **①** 游标移进 try 内、存储成功才推进(失败下轮重试)。✓
- **③** 主循环 `_run_monitor_cycle` 包 try/except,一轮异常不崩 daemon。✓
- **④** `start_daemon` 加 `data/daemon.lock`(fcntl flock),已占用拒启。✓
- **②(半)** `_pending_image_download_todo_count` 由"只数 today"改为"回看最近 7 天(跨月也读)",pending 不再隔天弃疗。✓
- 测试:cursor/persist 仍绿;daemon_image_selection/polling_strategy 基线就有 3 个失败(测试与代码漂移,非本次引入,且与上述改动正交)。

### ⚠️ 深挖发现:媒体端到端是子系统级残缺,不是 tweak(② 的另一半 + ⑤)
读码实证,媒体重试/解析链路**多处断裂**:
1. **两套 todo,好的那套是死的**:`SentinelTodoStore`(有 read_pending days_back=3 + mark_completed 完成追踪)设计完整,但 `append_pending` **全仓零调用** → `data/sentinel_todos` 不存在 → `_process_pending_todos` 永远空转 no-op。真正被填的是 naive 的 `image_todo_store`(只 append、无完成追踪)。
2. **todo 记录无图片身份**:image_download_todos 里就是 ledger 行(`seq/sender/time/msg-type/msg-content空/msg-path=未下载`),**没有 image_md5/local_id** → 拿不到身份没法重解析。
3. **下载后不回写 ledger**:即使图后来下来了,没有任何 pass 把 ledger 行的 `msg-path` 从"未下载"改成真实路径(`cli/main.py` 只在首次 ingest 时解析)。
4. **下游边界**:真正显示 `waiting_media` 的是 sop-data-hub;就算 wx-ops 回写了 ledger,sop-data-hub 要不要 re-ingest 是另一半。

→ **端到端修复 = 跨两仓 ~4 处连改**(todo 带 md5/local_id → 重解析 pass → 回写 ledger jsonl → 下游 re-ingest)。属功能级重建,且**无活的微信环境难验证下载→解析**。**建议作为独立聚焦工单做,不在本次盲改。**

### ⑥ UI sleep(可优化)— 建议**不盲改**
wechat.py 的 sleep 多为 #146 事故后的必要缓冲。可省的(滚动/搜索/最小化)单次仅省零点几秒,而发送频率很低(每天几次 excel);**收益极低**,但 UI 时序是 **#146 真实事故区**、且**无活微信无法验证**收紧不会重新引入"粘贴丢空/发送失败"。**建议保持现状**,或将来你能盯着 UI 时逐项实测再收。

### 小结
红线(①③④)+ ②回看窗口 已落地;媒体端到端(⑤+②另一半)是独立功能级重建;⑥不值当冒险盲改。

---
## 进展2(2026-06-24,用户决定"先2后3":硬上⑤,再做⑥)

### ✅ ⑤ 媒体重解析下载链已闭合(wx-ops-agent commit `a685ca7`)
关键发现 ledger seq 在 line634 已赋值、todo 拷贝自带 → todo→ledger 对应键(month+seq)现成,不 fragile。实现:
- image_todo 创建时带 image_md5/image_key/local_id/_ledger_month/todo_key + ledger seq;
- `ImageTodoStore.read_pending`(跨日 + 排除 resolved)/ `mark_resolved`;
- `ChatRecordStore.update_msg_path`(临时文件 + os.replace 原子改某行 msg-path,非命中行原样,绝不损坏 ledger);
- `_process_pending_todos` 改读 image_todo(非死 sentinel)→ 重解析成功 → **回写 ledger msg-path** + mark_resolved;
- pending 计数统一走 read_pending(②跨日 + ⑤排除 resolved → 图下来后停 pull)。
- 守门单测 4 个(`test_media_reresolve_chain.py`):原子改写只动目标行/含特殊字符不坏、跨日 pending、resolved 后排除、窗口外丢弃。daemon 已重启。
- ⚠️ "下载→解析"那步依赖活微信、本地不可验证;逻辑链已通。

### ✅ ⑥ UI sleep 收紧(wx-ui-bridge commit `98e9caf`)— 只收无风险的
- 收:wait_ready 轮询 0.3→0.2、minimize 0.3→0.15(都无后续依赖)。
- **刻意不动**:滚动 1.0(收紧反噬 ⑤ 媒体下载)、搜索结果 1.2(误发风险)、select 0.6/input 0.25/粘贴 0.15(#146 红线)。

### ⏳ 剩最后一半:下游 sop-data-hub re-ingest(另一仓)
wx-ops 现在图下来后会回写 ledger msg-path;但 sop-data-hub 按 cursor 读新行,**同 seq 被更新的行可能不会重读** → message_inbox 的 `waiting_media→ready` 还要在 sop-data-hub 侧接(检测 ledger 行 msg-path 由"未下载"变真实路径 → 重 ingest)。这是端到端的最后一环,独立做。

### ✅ 下游 re-ingest —— 核实后发现**早已建好,无需新代码**
读 sop-data-hub:`source_watcher` 每次重读全部 ledger 行;`message_inbox.upsert_message_inbox_event` 命中已存在记录就 **UPDATE(含 media_status/msg_path/processing_status)**;`run_live_service._retry_waiting_media`(R59.1,`_WAITING_MEDIA_MAX_RETRIES=200`)每轮重读 ledger、重建 event、`processing_status` 不再是 waiting_media 就 `process_event_once` 重处理 + 标 done。
- 实测 index:**281 条 2026-06 waiting 正在主动重试**。它们一直不成功的唯一原因 = wx-ops 从不回写 ledger(⑤ 的 bug)→ 重读永远"未下载"。**⑤ 一补,这条已建好的链自动闭合**,下游零改动。

### ⚠️ 真正剩下的根:message_id 串号(独立工单)
index 里 `铁晟:2026-04:1637`(abandoned)铁证:wx_1637 本是 6 月的,被钉成 2026-04 key → 读错 source_file → 永久 abandoned。**1927 条里 1600 abandoned 绝大多数是串号牺牲品**(`run_live_service.py:431` 注释自承"跨月 source_file 撞名…无脑 retry 永不成功")。message_id = `wx_{local_id/seq}`,而 seq 跨月重用 → 串号。这是检装单图不落候选 + 幽灵触发器 + retry abandoned 的**共同根因**,应作独立工单根治(message_id 加时间维度 / 唯一键)。

### 结案状态
①②③④⑤(wx-ops)+ ⑥(wx-ui-bridge)全部落地;下游 re-ingest 核实为**已建好,⑤ 补齐即闭环,零改动**;改不动的(微信懒加载)如实标注。**端到端打通(对正确 keying 的新图)**。剩 message_id 串号是独立根因工单。本工单结案。
