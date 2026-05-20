-- Phase 1 — 九三大豆 V3 架构 schema 扩充
-- 不修改生产库，只扩展 jiusan_cycle.db

-- ======== 1. 修改 jiusan_cycle_trains ========
-- 增加发车窗口、目标编组、是否返港口等字段
ALTER TABLE jiusan_cycle_trains ADD COLUMN departure_window TEXT;
  -- JSON: {"min": "06:00", "max": "10:00", "days": ["Mon","Tue","Wed","Thu","Fri"]}
ALTER TABLE jiusan_cycle_trains ADD COLUMN trains_per_day INTEGER DEFAULT 1;
ALTER TABLE jiusan_cycle_trains ADD COLUMN target_wagon_count INTEGER;
ALTER TABLE jiusan_cycle_trains ADD COLUMN return_to_port INTEGER DEFAULT 1;

-- ======== 2. jiusan_resource_pool — 资源池快照 ========
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
    source          TEXT DEFAULT 'auto',
    notes           TEXT,
    UNIQUE(pool_type, last_updated)
);

-- ======== 3. jiusan_train_composition — 列编组组成变更记录 ========
CREATE TABLE IF NOT EXISTS jiusan_train_composition (
    id              TEXT PRIMARY KEY,
    train_id        TEXT NOT NULL,
    round_no        INTEGER NOT NULL,
    car_no_list     TEXT,            -- JSON array
    container_no_list TEXT,           -- JSON array
    wagon_count     INTEGER,
    container_count INTEGER,
    change_reason   TEXT DEFAULT 'initial',
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(train_id) REFERENCES jiusan_cycle_trains(id)
);

-- ======== 4. jiusan_tracking_status — 95306 轨迹实时缓存 ========
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

-- ======== 5. jiusan_warnings — 预警事件 ========
CREATE TABLE IF NOT EXISTS jiusan_warnings (
    id              TEXT PRIMARY KEY,
    warning_time    TEXT NOT NULL,
    warning_type    TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    context_json    TEXT,
    acknowledged    INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_warn_time ON jiusan_warnings(warning_time);
CREATE INDEX IF NOT EXISTS idx_warn_type ON jiusan_warnings(warning_type);

-- ======== 6. jiusan_shipment_plan_days — 发运计划按日明细 ========
CREATE TABLE IF NOT EXISTS jiusan_shipment_plan_days (
    id              TEXT PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    day_of_week     TEXT NOT NULL,
    seq_no          INTEGER,
    container_trains INTEGER DEFAULT 0,
    container_wagons INTEGER DEFAULT 0,
    bulk_grain_wagons INTEGER DEFAULT 0,
    estimated_tons   REAL,
    notes           TEXT,
    FOREIGN KEY(plan_id) REFERENCES jiusan_shipment_plans(id)
);

-- ======== 7. 填充种子数据 — 资源池初始状态 ========
-- 集装箱池：当前项目已知 TBJU/TBCU 路用箱约 212 只（估算值，需用户确认）
INSERT OR IGNORE INTO jiusan_resource_pool (id, pool_type, current_total, available_qty, in_use_qty, in_repair_qty, loaned_out_qty, base_qty, last_updated, source, notes)
VALUES ('container_pool_seed', 'container', 212, 0, 108, 0, 0, 200, '2026-05-20T15:42:00', 'manual', '初始配置，列二54车108箱在途，列一返回中');

-- 车体池：C70E/C70EH 敞车约 106 辆（估算值，需用户确认）
INSERT OR IGNORE INTO jiusan_resource_pool (id, pool_type, current_total, available_qty, in_use_qty, in_repair_qty, loaned_out_qty, base_qty, last_updated, source, notes)
VALUES ('wagon_pool_seed', 'wagon', 106, 0, 54, 0, 0, 100, '2026-05-20T15:42:00', 'manual', '初始配置，列二54车在途，散粮40车已到站待返空');

-- ======== 8. 填充种子数据 — 发运计划按日明细 ========
-- 计划B：集装箱1天1列，散粮按需，厂耗5500吨/天
INSERT OR IGNORE INTO jiusan_shipment_plan_days (id, plan_id, day_of_week, seq_no, container_trains, container_wagons, bulk_grain_wagons, estimated_tons, notes)
VALUES
('plan_b_mon', 'plan_B', 'Mon', 1, 1, 54, 0, 3780, '集装箱1列约54车'),
('plan_b_tue', 'plan_B', 'Tue', 2, 1, 54, 0, 3780, '集装箱1列约54车'),
('plan_b_wed', 'plan_B', 'Wed', 3, 1, 54, 0, 3780, '集装箱1列约54车'),
('plan_b_thu', 'plan_B', 'Thu', 4, 1, 54, 0, 3780, '集装箱1列约54车'),
('plan_b_fri', 'plan_B', 'Fri', 5, 1, 54, 0, 3780, '集装箱1列约54车'),
('plan_b_sat', 'plan_B', 'Sat', 6, 0, 0, 0, 0, '周末无计划'),
('plan_b_sun', 'plan_B', 'Sun', 7, 0, 0, 0, 0, '周末无计划');

-- ======== 9. 填充种子数据 — 列编组组成（列二） ========
INSERT OR IGNORE INTO jiusan_train_composition (id, train_id, round_no, car_no_list, container_no_list, wagon_count, container_count, change_reason, notes)
VALUES ('comp_02_01', 'container_train_02', 1, NULL, NULL, 54, 108, 'initial', '2026-05-20 15:42 发车，待填写车号/箱号清单');

-- ======== 10. 填充种子数据 — tracking_status（列二 54 车） ========
-- 从 95306 实际数据导入当前 54 车状态
-- 全部同为 已发车(40) @ 15:42 高桥镇
INSERT OR IGNORE INTO jiusan_tracking_status (id, car_no, ydid, train_id, status_code, status_name, latest_event, latest_event_time, current_node, departed_at, is_on_way)
SELECT
    'ts_' || car_no,
    car_no,
    ydid,
    'container_train_02',
    status_code,
    status_name,
    '已发车（离开高桥镇）',
    COALESCE(departed_at, latest_event_time),
    '高桥镇',
    departed_at,
    1
FROM (
    SELECT car_no, ydid, status_code, status_name, departed_at, latest_event_time
    FROM shipments
    WHERE cargo_name = '大豆'
      AND origin_name = '高桥镇'
      AND destination_name = '新台子'
      AND ticketed_at >= '2026-05-20'
      AND ticketed_at < '2026-05-21'
      AND transport_mode_name = '集装箱运输'
    ORDER BY car_no
    LIMIT 54
);

-- ======== 11. 填充种子数据 — 库存预警 ========
INSERT OR IGNORE INTO jiusan_warnings (id, warning_time, warning_type, severity, message)
VALUES ('warn_inv_01', '2026-05-20T00:00:00', 'inventory_redline', 'critical', '库存 5,000 吨已跌破红线 8,000 吨');
