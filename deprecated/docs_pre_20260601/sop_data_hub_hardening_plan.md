# SOP Data Hub Hardening Plan

> Updated 2026-05-29 after R43 Phase Review.
> 目标：吉林金钢单项目自动运行

## Current Score: 2.1 / 5.0

## P0: 系统闭环最低要求

| # | 能力 | 状态 | 轮次 |
|---|------|:--:|:--:|
| P0-1 | DB schema migration (11 列) | ✅ done | R39+R41 |
| P0-2 | enrich_release_batch executor | ✅ done | R41 |
| P0-3 | 95306 query window executor | ✅ done | R42 |
| P0-4 | executor_status registry sync | ❌ todo | R44 |
| P0-5 | create_wagon_shipments executor | ❌ todo | R45 |
| P0-6 | 消息去重 (group_id + seq) | ❌ todo | R46 |
| P0-7 | 定时 95306 轮询 + confirmed_received | ❌ todo | R47 |

## P1: 业务自动化增强

| # | 能力 | 状态 | 轮次 |
|---|------|:--:|:--:|
| P1-1 | Excel generation (data-driven) | ❌ todo | R48 |
| P1-2 | Factory JSON generation | ❌ todo | R48 |
| P1-3 | Report delivery (telegram/wechat) | ❌ todo | R49 |
| P1-4 | Dashboard_state auto progression | ❌ todo | R50 |
| P1-5 | GROU005/013 fallback routing | ❌ todo | R46 |

## P2: 多项目和运维增强

| # | 能力 | 状态 |
|---|------|:--:|
| P2-1 | 朝钢 SOP YAML 补全 | backlog |
| P2-2 | 中唐 YAML 补全 | backlog |
| P2-3 | 合同 intelligence | backlog |
| P2-4 | 集成验收（蓝鳍/长航滨海/木森17） | R51 |
| P2-5 | PDF generation | backlog |

## Shortest Path to Verify departure_flow

```
R44: executor_status registry sync
R45: create_wagon_shipments executor
R46: end-to-end verification (蓝鳍 departure text)
```

## Next

- R44: sync executor_status registry
- R45: create_wagon_shipments
