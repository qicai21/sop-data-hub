"""release_batch lifecycle 状态机(#122,2026-06-07)。

lifecycle = batch 的 8 个状态 + 系统行为门控。状态推进由 chain 各步/closeout
触发器自动驱动(见 #128 / #127),拒绝匹配 / 归档由消费方按 phase 查询。

8 个状态(沿用 release_batches.dispatch_status 列名,枚举值升级):

    pending_freight     创建,合同/订单/品名缺(老 pending_completion)
        ↓ freight_detail_enrichment 完成
    enriched            合同补齐,等装车
        ↓ 第一票 wagon ingest
    loading             有车在发,plan 未满
        ↓ dispatch_plan.allocated >= planned
    all_loaded          plan 满 — **拒绝新装车通知**(落 pending_match)
        ↓ wagon 全部 departed_at 非空
    tracking            在 95306 路上(loading→tracking 直跳也常见)
        ↓ wagon 全部 delivered_at 非空 / latest_stage_key >= 80
    delivered           车全到,等工厂签收
        ↓ 95306 latest_stage_key 全 >= 80 (用户:80 即视为收货)
    confirmed_received  合同结算锚点
        ↓ 自动 / 手动归档
    closed              归档

yaml lifecycle.mode 分叉:
- shipped_is_completed (朝阳):all_loaded → 直接跳 closed,不走 tracking
- full_track_to_received (jilin/中唐/九三):必须走完 tracking → delivered → confirmed_received

系统行为门控表:
| phase                | 配车配箱 | 接装车通知 | poll 95306 | dashboard 活跃 |
|----------------------|----------|------------|------------|----------------|
| pending_freight      | ✗       | ✗         | ✗         | ✓(灰)        |
| enriched             | ✓       | ✓         | ✗         | ✓             |
| loading              | ✓       | ✓         | ✓         | ✓             |
| all_loaded           | ✗       | **拒绝**  | ✓         | ✓             |
| tracking             | ✗       | ✗         | ✓         | ✓             |
| delivered            | ✗       | ✗         | ✗         | ✓             |
| confirmed_received   | ✗       | ✗         | ✗         | ✗             |
| closed               | ✗       | ✗         | ✗         | ✗             |
"""
from __future__ import annotations
from typing import Final


# ── 8 个枚举值 ─────────────────────────────────────────────────────
PENDING_FREIGHT: Final[str] = "pending_freight"
ENRICHED: Final[str] = "enriched"
LOADING: Final[str] = "loading"
ALL_LOADED: Final[str] = "all_loaded"
TRACKING: Final[str] = "tracking"
DELIVERED: Final[str] = "delivered"
CONFIRMED_RECEIVED: Final[str] = "confirmed_received"
CLOSED: Final[str] = "closed"

ALL_PHASES: Final[tuple[str, ...]] = (
    PENDING_FREIGHT, ENRICHED, LOADING, ALL_LOADED,
    TRACKING, DELIVERED, CONFIRMED_RECEIVED, CLOSED,
)


# ── 行为门控分组 ──────────────────────────────────────────────────

# 可被装车通知 match_release_batch 选中的 phase。其他 phase 命中 → 走
# #122b pending_match 兜底,等用户审。
PHASES_OPEN_TO_DEPARTURE_MATCH: Final[frozenset[str]] = frozenset({
    ENRICHED, LOADING,
})

# 还在 95306 路上,closeout 要持续 poll。
PHASES_ACTIVE_TRACKING: Final[frozenset[str]] = frozenset({
    LOADING, ALL_LOADED, TRACKING,
})

# dashboard 活跃区(默认看板筛选)。
PHASES_DASHBOARD_ACTIVE: Final[frozenset[str]] = frozenset({
    PENDING_FREIGHT, ENRICHED, LOADING, ALL_LOADED, TRACKING, DELIVERED,
})

# 终态:不再触发任何 chain,可归档。
PHASES_TERMINAL: Final[frozenset[str]] = frozenset({
    CONFIRMED_RECEIVED, CLOSED,
})


# ── 老枚举 → 新枚举迁移规则(#125 migration)──────────────────────
# 老:in_progress / completed / pending_completion / suspended / cancelled
LEGACY_MIGRATION_MAP: Final[dict[str, str]] = {
    "pending_completion": PENDING_FREIGHT,
    "in_progress": LOADING,           # 默认推到 loading;#128 触发器后续推到 all_loaded/tracking/delivered
    "completed": CONFIRMED_RECEIVED,  # 全部归 confirmed;#127 closeout 推到 closed
    "suspended": LOADING,             # 老逻辑里 suspended 跟 in_progress 同组(可接通知);先归 loading
    "cancelled": CLOSED,              # 老逻辑里 cancelled 是终态
}
