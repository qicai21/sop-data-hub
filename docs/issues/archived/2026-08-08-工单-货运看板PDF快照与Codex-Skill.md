# 工单：货运看板 PDF 快照与 Codex Skill

## 状态

已完成

## 使用场景

用户通过手机使用 Codex Remote 访问当前任务时，需要直接取得一份当前货运看板快照，
而不是依赖手机访问局域网地址或查看终端输出。

## 设计决定

- 快照格式采用 PDF：单文件、自包含、手机预览稳定，不依赖局域网持续在线。
- 数据源仍为只读 Web 货运看板 `http://127.0.0.1:8765/`，不复制业务查询逻辑。
- PDF 使用服务端冻结的 `/snapshot` 页面，一次取数后不再执行 5 秒轮询，保证快照时点一致。
- macOS Chrome 写完 PDF 后可能保持 Headless 主进程；导出器以完整 PDF EOF 为成功条件，
  随后仅终止本次隔离 profile 对应的进程组，不影响用户浏览器。
- 快照写入 `~/Library/Caches/Codex/sop-dashboard-snapshots/`，不污染桌面和项目仓库。
- 每次生成前清理超过 72 小时的历史快照，并由 launchd 每日执行一次兜底清理。
- 清理范围严格限定为该目录中 `freight-dashboard-*.pdf` 文件。
- 建立 Codex 个人 Skill，识别“查看货运看板/发送看板快照/手机看板”等请求，
  自动生成、校验并返回 PDF。

## 实施范围

- 为 Web 看板补充 A4 横向打印样式。
- 新增只读 PDF 快照导出与清理脚本。
- 新增每日清理 LaunchAgent 模板并加载。
- 增加清理边界、Chrome 调用和打印样式测试。
- 创建并校验个人 Skill `freight-dashboard-snapshot`。

## 验收标准

- 从运行中的看板生成可正常打开的 PDF，内容为生成时的真实看板状态。
- PDF 使用横向页面，表格和九三循环图不被横向裁断。
- 超过 72 小时的本工具历史 PDF 自动删除，其他文件不受影响。
- Skill 能生成并返回快照文件的绝对路径。
- 测试通过；受影响服务重启并通过健康检查；本轮改动单独提交。

## 执行记录（2026-08-08）

- Web 看板增加服务端静态 `/snapshot` 页面和 A4 横向打印样式。
- 新增 `scripts/export_dashboard_snapshot.py`：健康检查、隔离 Chrome profile、
  完整 PDF 检查、JSON 结果输出以及 72 小时限定清理。
- 新增并加载 `com.qicai21.sop-data-hub.dashboard-snapshot-cleanup`，每日 03:20 清理；
  首次运行退出码为 0。
- 创建个人 Skill `/Users/qicai21/.codex/skills/freight-dashboard-snapshot`，
  `quick_validate.py` 校验通过。
- 定向回归：`17 passed`。
- 真实快照为 2 页 A4 横向 PDF；使用 `pdftoppm` 渲染全部页面并目视确认项目表、
  九三循环图、系统状态无空白和横向裁切。
- 已重启 `com.qicai21.sop-data-hub.dashboard-web`，`/healthz` 返回 `ok`。
