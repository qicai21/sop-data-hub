-- =============================================================
-- 九三大豆循环运输资源账系统 - DDL Schema (Phase 1-2)
-- DB: ops-data-hub/data/jiusan_cycle.db
-- Phase 3 预留表: jiusan_resource_pool, jiusan_cycle_train_compositions
-- =============================================================
-- 注：SQLite CHECK 约束在低版本静默忽略，合法性由应用层 Python 校验保证

-- 3.2.2 资源变动事件 (Phase 1-2)
CREATE TABLE IF NOT EXISTS jiusan_resource_events (
    id              TEXT PRIMARY KEY,
    pool_type       TEXT NOT NULL,                 -- 'container' | 'wagon'
    event_type      TEXT NOT NULL,                 -- 'add' | 'remove' | 'transfer_in' | 'transfer_out' | 'repair' | 'return' |
                                                   -- 'loan_out' | 'loan_return' | 'damage' | 'scrap' | 'inventory_gain' | 'inventory_loss'
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

-- 3.2.3 循环列 (Phase 1-2)
CREATE TABLE IF NOT EXISTS jiusan_cycle_trains (
    id              TEXT PRIMARY KEY,              -- e.g. 'container_train_01', 'bulk_grain_train_01'
    train_type      TEXT NOT NULL,                 -- 'container' | 'bulk_grain_wagon'
    lot             TEXT,                          -- lot01 / lot02
    status          TEXT NOT NULL,                  -- 'forming' | 'loaded' | 'departed' | 'on_way' | 'arrived' | 'unloaded' | 'returning'
    current_round   INTEGER NOT NULL DEFAULT 1,
    transport_mode  TEXT,                          -- 'container' | 'bulk_grain_wagon'
    destination_line TEXT,                         -- '三三〇处专用线' | '九三集团铁岭大豆科技有限公司专用线'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3.2.4 循环列运行记录 (Phase 1-2)
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

-- 3.2.6 状态快照 (Phase 1-2)
CREATE TABLE IF NOT EXISTS jiusan_snapshots (
    id              TEXT PRIMARY KEY,
    snapshot_date   TEXT NOT NULL,
    snapshot_time   TEXT,
    source          TEXT NOT NULL,                  -- 'morning_report' | 'calc_from_flow' | 'spot_report' | 'manual'
    source_ref      TEXT,
    fields_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, snapshot_time, source)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON jiusan_snapshots(snapshot_date);

-- 3.2.7 期间流量 (Phase 1-2)
CREATE TABLE IF NOT EXISTS jiusan_flows (
    id              TEXT PRIMARY KEY,
    flow_date       TEXT NOT NULL,
    flow_type       TEXT NOT NULL,                 -- 'daily' | 'multi_day'
    source          TEXT NOT NULL,                  -- 'morning_report' | '95306_calc' | 'calc_from_snapshot' | 'manual'
    source_ref      TEXT,
    fields_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flow_date, flow_type, source)
);
CREATE INDEX IF NOT EXISTS idx_flow_date ON jiusan_flows(flow_date);

-- 3.2.8 资源调整记录 (Phase 1-2)
CREATE TABLE IF NOT EXISTS jiusan_adjustments (
    id              TEXT PRIMARY KEY,
    adj_date        TEXT NOT NULL,
    adj_type        TEXT NOT NULL,                 -- 'container_in' | 'container_out' | 'wagon_in' | 'wagon_out' |
                                                   -- 'loan_out' | 'loan_return' | 'repair' | 'damage' |
                                                   -- 'inventory_gain' | 'inventory_loss'
    quantity        INTEGER NOT NULL,
    pool_type       TEXT NOT NULL,                 -- 'container' | 'wagon'
    source          TEXT NOT NULL,                  -- 'morning_report' | 'manual' | 'spot_report'
    source_ref      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_adj_date ON jiusan_adjustments(adj_date);
CREATE INDEX IF NOT EXISTS idx_adj_type ON jiusan_adjustments(adj_type);

-- 3.2.9 发运计划版本 (Phase 1-2)
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

-- 3.2.10 厂家库存记录 (Phase 1-2)
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

-- 3.2.11 95306 扫描日志 (Phase 1-2)
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
