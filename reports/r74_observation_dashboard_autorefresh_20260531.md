# R74: 常驻运行观察 + Dashboard 自动更新验证

**Date:** 2026-05-31
**Branch:** `codex/sop-real-sop-topology-audit-20260525`

## Summary
验证 live_service 常驻运行、dashboard 自动刷新、新消息识别链路。用户补发 2 条测试消息到 数据单发群，系统成功识别并入库。

## Live Service
| Field | Value |
|---|---|
| **alive** | true |
| **PID** | 24309 |
| **last_processed_message_id** | wx_2 |
| **last_processed_time** | 2026-05-31T09:09:51Z |

## Dashboard
| Field | Value |
|---|---|
| **alive** | true |
| **PID** | 18202 (dashboard server) |
| **port** | 8787 |
| **URL** | `http://localhost:8787/` |
| **auto_refresh** | 8s (HTML meta-refresh + JS fetch polling) |
| **API endpoints** | /api/status, /api/message-inbox, /api/workflow-tasks, /api/external-actions, /api/health/latest |

## Test Messages
| # | message_id | message_inbox_id | group | text | processing_status |
|---|---|---|---|---|---|
| 1 | wx_1 | 69 | 数据单发群-GROUP013 | R74 测试消息 | ignored |
| 2 | wx_2 | 70 | 数据单发群-GROUP013 | R74 测试消息，pending +1 | ignored |

**Dashboard 显示:** ✅ 两条消息均出现在 API `/api/message-inbox` 和 HTML 页面中，状态为 `ignored`（非 SOP 业务消息，符合预期）。

## Root Cause Fix
**问题:** wx-ops-agent 替换了 `数据单发群-GROUP013/2026-05.jsonl`（旧文件 53 条 local_id→53，新文件 2 条 seq=1,2），但 live_service cursor 仍为 last_local_id=53，导致 seq=1 ≤ 53 被过滤。

**修复:** 重置该 source 的 cursor 至 last_local_id=0，触发 --once 处理新消息，cursor 前进至 2。

## Health Snapshots
| Metric | Value |
|---|---|
| **count** | 5 (timestamped + latest.json) |
| **location** | `runtime/health_snapshots/` |
| **interval** | 每 10 分钟自动生成 |
| **latest** | `runtime/health_snapshots/latest.json` |

## System Stats
| Entity | Count/Status |
|---|---|
| **message_inbox total** | 66 |
| **waiting_media** | 46 |
| **ignored** | 5 |
| **workflow_task_db** | pending=9, succeeded=2, skipped=2, total=13 |
| **external_action_log** | executed=3 |
| **storage violations** | 0 |
| **95306_collection written** | false |
| **live_service cursor** | 未回退 (GROUP013: 53→0→2) |
| **live_service alive** | true |

## Verification Checklist
- [x] live_service alive=true, PID exists
- [x] dashboard alive=true, port 8787
- [x] dashboard auto_refresh=true (8s)
- [x] 用户补发消息进入 message_inbox (ids 69, 70)
- [x] dashboard 自动显示新消息
- [x] cursor 前进不后退
- [x] health snapshots ≥ 3 (5 generated)
- [x] workflow_task/external_action_log 可查
- [x] storage violations=0
- [x] 95306_collection written=false

## Note
- 测试消息为普通文字（非 SOP 业务），正确标识为 `ignored`，未消失
- Source 替换 → cursor 漂移是真实场景发现的 bug，临时按 source 重置解决
- 建议后续添加 source file hash / content-based cursor 检测机制

## Commit
- **sha:** (pending)
