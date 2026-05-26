# Source Supervision Discovery Audit

## 结论

本次仅做源结构审计，不做实现、不做适配器、不改 runtime。

审计结果显示：`wx-ops-agent` 当前已经形成一条稳定的“微信 DB 原始消息 → 消息归一化 → 聊天账本/待办 JSONL → 图片落盘/ops-data-hub 挂载”的链路；其中业务判断与结构化入库逐步向 `ops-data-hub` 侧收敛，但代理侧仍保留兼容层与轮询/落盘职责。

---

## 1. 微信消息原始结构

### 原始查询字段
`wx-ops-agent` 从 WeChat message table 读取的原始行，当前可见字段主要包括：

- `local_id`
- `create_time`
- `send_time`
- `local_type`
- `real_sender_id`
- `packed_info_data_hex`
- `message_content_hex`
- `message_content`
- `source`

证据：
- `src/wechat_ops_agent/cli/main.py` 的 `fetch-contact-messages` 查询 SQL
- `src/wechat_ops_agent/db/message_normalizer.py` 的 `normalize_message_row()`

### 归一化后的消息结构
归一化后，消息会补齐/派生出这些关键字段：

- `local_type`
- `create_time`
- `send_time`
- `message_kind`
- `content`
- `display_text`
- `raw_content`
- `row_text`
- `sender_username`
- `sender_name`
- `local_id`
- `server_id`
- `is_send`
- `source`
- `message_key`
- `image_md5` / `image_key` / `image_dat_name`
- `video_md5`
- `file_name` / `file_size` / `file_ext`
- `voice_text`
- `revoke_text`

其中图片消息还会从 `packed_info_data` / `packed_info_data_hex` 中提取 `image_md5`、`image_dat_name` 等标识。

---

## 2. 图片保存位置

### 默认图片根目录
`wx-ops-agent` 的默认图片根目录是：

- `~/Documents/bussiness-artifacts/wechat_images`

证据：
- `src/wechat_ops_agent/storage/paths.py`
- `src/wechat_ops_agent/storage/image_store.py`

### 实际保存规则
图片解析后，当前保存到：

- `<wechat_images_root>/<群名>/<YYYY-MM>/<local_id>_<image_md5>.jpg`

如果消息本身已经有可用本地图片路径，则直接复用该路径并写入 `saved_image_path` / `msg_path`。

如果图片未下载完成，则账本中保留：

- `msg-path: "未下载"`

### 轮询时的临时/影子路径
在扫描 WeChat DB 时，daemon 会在 `/tmp` 下创建 shadow copy，例如：

- `/tmp/shadow_quick_<message_db_name>`

### WeChat 原始媒体目录
daemon 组图索引和媒体解析还会读取 WeChat 本地消息目录：

- `<account_root>/msg/attach/<group_md5(group_wxid)>`
- `<account_root>/msg/video/`
- `<account_root>/msg/` 下的文件路径

---

## 3. JSON 保存位置

### 聊天记录 JSONL
聊天记录账本默认写到：

- `data/chat_records/<群名>/<YYYY-MM>.jsonl`

证据：
- `src/wechat_ops_agent/chat_records/store.py`

### 图片/视频待办 JSONL
未下载或需要重试的图片/视频会分别写入：

- `data/chat_records/<群名>/image_download_todos/<YYYY-MM>.jsonl`
- `data/chat_records/<群名>/video_download_todos/<YYYY-MM>.jsonl`

证据：
- `src/wechat_ops_agent/chat_records/image_todo.py`
- `src/wechat_ops_agent/chat_records/video_todo.py`

### 群成员映射 / 未映射成员 JSONL
群成员解析结果会落到：

- `data/group_member_maps/...`
- `data/group_member_maps/unmapped_members/...`

### 运行时 cursor / 状态 JSON
daemon 还会维护：

- `data/last_seen_timers.json`
- `data/daemon.stop`

---

## 4. 消息 metadata

当前消息链路中的 metadata 主要分两层：

### A. 账本层 metadata
写入 `chat_records/*.jsonl` 时，核心字段是：

- `seq`
- `sender`
- `sender-wxid`
- `time`
- `msg-type`
- `msg-content`
- `msg-path`
- `remark`

### B. 归一化/路由层 metadata
在 daemon 和业务路由器中，还会携带：

- `sender_name`
- `sender_wxid`
- `sender_member_id`
- `sender_resolution_status`
- `group_name`
- `group_wxid`
- `local_id`
- `server_id`
- `image_md5`
- `image_key`
- `image_dat_name`
- `video_md5`
- `file_name`
- `artifact_path`
- `saved_image_path`
- `ops_data_hub_category`
- `ops_data_hub_extraction_saved_path`

结论：当前的消息 metadata 已经足够支撑“消息归一化、群成员解析、图片路由、待办重试、账本落盘、ops-data-hub 挂载”。

---

## 5. message_id 来源

当前代码里**没有一个统一命名为 `message_id` 的权威字段**。

实际用于消息唯一性/定位的主来源是：

- `local_id`：来自 WeChat message table 的行内 ID，是当前最核心的消息定位字段
- `server_id`：作为辅助字段被归一化出来
- `message_key`：从原始行字段提取的补充键
- 图片场景里还会结合 `image_md5` / `image_dat_name`

在图片待办与图片索引里，常见的定位键是：

- `local_id`
- `group_wxid + local_id + image_hash + reason` 组成的 `todo_key`

所以如果这里说“message_id”，当前实现更接近：

- `local_id` 为主
- 其他字段为辅助

---

## 6. 当前运行方式

### 启动入口
`wx-ops-agent` 当前的 daemon 启动方式分两层：

- `daemon-auto`
  - 后台拉起
  - 固定写入 `data/runtime-logs/daemon-auto.log`
  - 默认调用 `run-daemon`
- `run-daemon`
  - 常驻轮询
  - 通过 `--interval`、`--ui-mode`、`--doc-detail-mode` 控制行为

### UI 模式
支持：

- `auto`
- `manual`
- `silent`

当前代码语义：

- `auto`：会做 UI 拉图/同步
- `manual` / `silent`：跳过自动 UI 拉取，仅同步 DB 日志

### 与 ops-data-hub 的耦合方式
`run-daemon` 会尝试从 `ops-data-hub` 加载全局配置：

- `daemon_interval`
- `daemon_message_limit`
- `tracking_rules_path`

然后将 ProjectSOP 派生出来的监控群任务转成 `listener_objects` / `monitored_groups` 使用。

---

## 7. 当前输出目录

### `wx-ops-agent` 运行时输出
当前运行时根目录是仓库根下的：

- `data/`

其下最关键的输出包括：

- `data/chat_records/`
- `data/runtime-logs/`
- `data/reports/`
- `data/last_seen_timers.json`

### 图片输出目录
`ops-data-hub` 配置中的当前图片输出相关目录是：

- `wechat_images_dir: /Users/qicai21/Documents/bussiness-artifacts/wechat_images`
- `classified_output_dir: /Users/qicai21/Documents/bussiness-artifacts/wechat_images`
- `extraction_output_dir: /Users/qicai21/Documents/bussiness-artifacts/wechat_images/extractions`

这意味着：

- 微信原图/已保存图的主落点在 `wechat_images_dir`
- 结构化提取结果在 `extractions/`
- 代理侧账本与待办仍保留在 `data/chat_records/`

---

## 8. 是否已有日志

有，且已存在多类日志/记录：

- `data/runtime-logs/daemon-auto.log`
- `data/chat_records/<群名>/<YYYY-MM>.jsonl`
- `data/group_member_maps/unmapped_members/...`

`daemon-auto.log` 中可见：

- 持续 poll cycle
- cursor 从 ledger 解析
- DB 查询结果为 `0 row(s)` 的轮询状态
- `UI cleanup is delegated to the external action bridge`

说明当前日志链路是活的，且已有稳定的轮询/落盘行为。

---

## 附：审计依据文件

- `ops-data-hub/config/settings.yaml`
- `ops-data-hub/src/ops_hub/config.py`
- `ops-data-hub/src/ops_hub/runner.py`
- `ops-data-hub/src/ops_hub/data_agent/agent.py`
- `wx-ops-agent/src/wechat_ops_agent/storage/paths.py`
- `wx-ops-agent/src/wechat_ops_agent/chat_records/store.py`
- `wx-ops-agent/src/wechat_ops_agent/chat_records/ledger.py`
- `wx-ops-agent/src/wechat_ops_agent/chat_records/media_resolver.py`
- `wx-ops-agent/src/wechat_ops_agent/db/message_normalizer.py`
- `wx-ops-agent/src/wechat_ops_agent/cli/main.py`
- `wx-ops-agent/src/wechat_ops_agent/cli/daemon.py`
- `wx-ops-agent/data/runtime-logs/daemon-auto.log`
- `wx-ops-agent/data/chat_records/铁晟业务工作群/2026-05.jsonl`
- `wx-ops-agent/data/group_member_maps/unmapped_members/铁晟业务工作群/2026-05-26.jsonl`
