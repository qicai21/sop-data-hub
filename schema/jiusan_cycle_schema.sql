-- =============================================================
-- 九三大豆循环运输资源账系统 - DDL Schema (V3)
-- DB: sop-data-hub/data/jiusan_cycle.db
-- V3 架构：循环运输资源账 + 运行态势 + 运力计划 + 库存风险预警
-- =============================================================
-- 架构变更：
-- V3 新增：jiusan_resource_pool, jiusan_tracking_status
--          jiusan_train_composition, jiusan_warnings
--          jiusan_shipment_plan_days
-- V3 修改：jiusan_cycle_trains (增加 departure_window 等字段)
-- V3 移除：冲突检测逻辑（运行状态以 95306 最新事件为准单向映射）
-- =============================================================

-- 4.2 资源变动事件
CREATE TABLE IF NOT EXISTS jiusan_resource_events (
    id              TEXT PRIMARY KEY,
    pool_type       TEXT NOT NULL,                 -- 'container' | 'wagon'
    event_type      TEXT NOT NULL,                 -- 'add' | 'remove' | 'transfer_in' | 'transfer_out' |
                                                   -- 'repair' | 'return' | 'loan_out' | 'loan_return' |
                                                   -- 'damage' | 'scrap' | 'inventory_gain' | 'inventory_loss'
    prev_status     TEXT,
    new_status      TEXT,
    prev_node       TEXT,
    new_node        TEXT,
    prev_train_id   TEXT,
    new_train_id    TEXT,
    quantity        INTEGER NOT NULL DEFAULT 1,
    event_time      TEXT NOT NULL,
    source          TEXT,                           -- 'manual' | '95306_sync' | 'morning_report' | 'spot_report'
    source_ref      TEXT,                           -- 来源参考（报告ID、消息ID、95306 ydid）
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_res_events_type ON jiusan_resource_events(event_type);
CREATE INDEX IF NOT EXISTS idx_res_events_time ON jiusan_resource_events(event_time);

-- 5.1 循环列定义
CREATE TABLE IF NOT EXISTS jiusan_cycle_trains (
    id              TEXT PRIMARY KEY,              -- e.g. 'container_cycle_train_01', 'bulk_grain_train_01'
    train_type      TEXT NOT NULL,                 -- 'container' | 'bulk_grain_wagon'
    lot             TEXT,                          -- lot01 / lot02
    status          TEXT NOT NULL,                 -- 'forming' | 'loaded' | 'departed' | 'on_way' |
                                                   -- 'arrived' | 'unloaded' | 'returning' | 'returned'
    current_round   INTEGER NOT NULL DEFAULT 1,
    transport_mode  TEXT,                          -- 'container' | 'bulk_grain_wagon'
    destination_line TEXT,                         -- '三三〇处专用线' | '九三集团铁岭大豆科技有限公司专用线'
    -- V3 新增字段
    departure_window TEXT,                         -- JSON: {"min":"06:00","max":"10:00","days":["Mon",...]}
    trains_per_day  INTEGER DEFAULT 1,
    target_wagon_count INTEGER,
    return_to_port  INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.2 循环列运行记录
CREATE TABLE IF NOT EXISTS jiusan_cycle_train_runs (
    id              TEXT PRIMARY KEY,
    train_id        TEXT NOT NULL,
    round_no        INTEGER NOT NULL,
    wagon_count     INTEGER,
    container_count INTEGER,
    total_weight    REAL,
    depart_time     TEXT,
    arrive_time     TEXT,
    unload_time     TEXT,
    return_time     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    source          TEXT,
    source_ref      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(train_id) REFERENCES jiusan_cycle_trains(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_train ON jiusan_cycle_train_runs(train_id, round_no);

-- 5.3 列编组组成变化记录（V3 新增）
CREATE TABLE IF NOT EXISTS jiusan_train_composition (
    id              TEXT PRIMARY KEY,
    train_id        TEXT NOT NULL,
    round_no        INTEGER NOT NULL,
    car_no_list     TEXT,                          -- JSON array of car numbers
    container_no_list TEXT,                        -- JSON array of container numbers
    wagon_count     INTEGER,
    container_count INTEGER,
    change_reason   TEXT DEFAULT 'initial',        -- 'initial' | 'damage_replaced' | 'added' | 'removed'
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(train_id) REFERENCES jiusan_cycle_trains(id)
);

-- 6.1 状态快照
CREATE TABLE IF NOT EXISTS jiusan_snapshots (
    id              TEXT PRIMARY KEY,
    snapshot_date   TEXT NOT NULL,
    snapshot_time   TEXT,
    source          TEXT NOT NULL,                 -- 'morning_report' | 'calc_from_flow' | 'spot_report' | 'manual'
    source_ref      TEXT,
    fields_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, snapshot_time, source)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON jiusan_snapshots(snapshot_date);

-- 6.2 期间流量
CREATE TABLE IF NOT EXISTS jiusan_flows (
    id              TEXT PRIMARY KEY,
    flow_date       TEXT NOT NULL,
    flow_type       TEXT NOT NULL,                 -- 'daily' | 'multi_day'
    source          TEXT NOT NULL,                 -- 'morning_report' | '95306_calc' | 'calc_from_snapshot' | 'manual'
    source_ref      TEXT,
    fields_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flow_date, flow_type, source)
);
CREATE INDEX IF NOT EXISTS idx_flow_date ON jiusan_flows(flow_date);

-- 6.3 资源调整
CREATE TABLE IF NOT EXISTS jiusan_adjustments (
    id              TEXT PRIMARY KEY,
    adj_date        TEXT NOT NULL,
    adj_type        TEXT NOT NULL,                 -- 'container_in' | 'container_out' | 'wagon_in' | 'wagon_out' |
                                                   -- 'loan_out' | 'loan_return' | 'repair' | 'damage' |
                                                   -- 'inventory_gain' | 'inventory_loss'
    quantity        INTEGER NOT NULL,
    pool_type       TEXT NOT NULL,                 -- 'container' | 'wagon'
    source          TEXT NOT NULL,                 -- 'morning_report' | 'manual' | 'spot_report'
    source_ref      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_adj_date ON jiusan_adjustments(adj_date);
CREATE INDEX IF NOT EXISTS idx_adj_type ON jiusan_adjustments(adj_type);

-- 6.4 资源池快照（V3 新增）
CREATE TABLE IF NOT EXISTS jiusan_resource_pool (
    id              TEXT PRIMARY KEY,
    pool_type       TEXT NOT NULL CHECK(pool_type IN ('container', 'wagon')),
    current_total   INTEGER NOT NULL,
    available_qty   INTEGER,
    in_use_qty      INTEGER,
    in_repair_qty   INTEGER DEFAULT 0,
    loaned_out_qty  INTEGER DEFAULT 0,
    base_qty        INTEGER,
    last_updated    TEXT NOT NULL,
    source          TEXT DEFAULT 'auto',           -- 'auto' | 'manual'
    notes           TEXT,
    UNIQUE(pool_type, last_updated)
);

-- 6.5 95306 轨迹实时缓存（V3 新增）
CREATE TABLE IF NOT EXISTS jiusan_tracking_status (
    id              TEXT PRIMARY KEY,
    car_no          TEXT NOT NULL,
    ydid            TEXT,
    train_id        TEXT,
    status_code     TEXT,
    status_name     TEXT,
    latest_event    TEXT,
    latest_event_time TEXT,
    current_node    TEXT,
    arrived_at      TEXT,
    departed_at     TEXT,
    delivered_at    TEXT,
    is_on_way       INTEGER DEFAULT 1,
    source_scan_id  TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(car_no, ydid)
);
CREATE INDEX IF NOT EXISTS idx_track_car ON jiusan_tracking_status(car_no);
CREATE INDEX IF NOT EXISTS idx_track_train ON jiusan_tracking_status(train_id);
CREATE INDEX IF NOT EXISTS idx_track_on_way ON jiusan_tracking_status(is_on_way);

-- 6.6 预警事件（V3 新增）
CREATE TABLE IF NOT EXISTS jiusan_warnings (
    id              TEXT PRIMARY KEY,
    warning_time    TEXT NOT NULL,
    warning_type    TEXT NOT NULL,                 -- 'inventory_redline' | 'plan_shortfall' |
                                                   -- 'train_delayed' | 'pool_depleted' | 'flow_imbalance'
    severity        TEXT NOT NULL DEFAULT 'info',  -- 'info' | 'warning' | 'critical'
    message         TEXT NOT NULL,
    context_json    TEXT,
    acknowledged    INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_warn_time ON jiusan_warnings(warning_time);
CREATE INDEX IF NOT EXISTS idx_warn_type ON jiusan_warnings(warning_type);

-- 8. 发运计划版本
CREATE TABLE IF NOT EXISTS jiusan_shipment_plans (
    id              TEXT PRIMARY KEY,              -- e.g. 'plan_A', 'plan_B'
    name            TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    plan_details_json TEXT NOT NULL,
    superseded_by   TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_plans_active ON jiusan_shipment_plans(is_active, effective_from);

-- 8.1 发运计划按日明细（V3 新增）
CREATE TABLE IF NOT EXISTS jiusan_shipment_plan_days (
    id              TEXT PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    day_of_week     TEXT NOT NULL,                 -- 'Mon'|'Tue'|'Wed'|'Thu'|'Fri'|'Sat'|'Sun'
    seq_no          INTEGER,
    container_trains INTEGER DEFAULT 0,
    container_wagons INTEGER DEFAULT 0,
    bulk_grain_wagons INTEGER DEFAULT 0,
    estimated_tons   REAL,
    notes           TEXT,
    FOREIGN KEY(plan_id) REFERENCES jiusan_shipment_plans(id)
);

-- 9. 厂家库存
CREATE TABLE IF NOT EXISTS jiusan_factory_inventory (
    id              TEXT PRIMARY KEY,
    record_date     TEXT NOT NULL,
    opening_stock   REAL,
    line_in_qty     REAL,
    other_source_in_qty REAL DEFAULT 0,
    consumption     REAL,
    closing_stock   REAL,
    adjustment      REAL DEFAULT 0,
    red_line        REAL,
    days_supported  REAL,
    source          TEXT,
    source_ref      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(record_date, source)
);
CREATE INDEX IF NOT EXISTS idx_inv_date ON jiusan_factory_inventory(record_date);

-- 7. 95306 扫描日志
CREATE TABLE IF NOT EXISTS jiusan_95306_scan_log (
    id              TEXT PRIMARY KEY,
    scan_time       TEXT NOT NULL,
    scan_type       TEXT NOT NULL,                 -- 'auto' | 'manual'
    new_records     INTEGER DEFAULT 0,
    matched_to_lot  INTEGER DEFAULT 0,
    matched_to_train INTEGER DEFAULT 0,
    errors          TEXT,
    details_json    TEXT,
    duration_seconds REAL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
