-- R80: cycle_train schema — 集装箱循环列识别与跟踪
-- Date: 2026-06-10
-- Origin: 九三大豆昆娜+玛格丽特实证,详见
--   data/contracts/jiusan_soybean/cycle_freight_observation_v1.md
--
-- 设计动机:
--   集装箱业务中存在"循环列"概念:固定车号编组(~50 辆) × 固定周期(~3 天) ×
--   多列并行错开发车。列在港 - 在路 - 在 330 - 返空之间循环,通过同日发车
--   的车号签名可识别列编组。本 schema 提供列定义 + 成员关联的最小可行表。
--
-- 两层表:
--   cycle_trains            — 列定义,1 row = 1 列
--   cycle_train_membership  — 列-车号关联,1 row = 1 辆车在 1 列中
--
-- 用法:
--   1) 通过 95306 wagon_container_shipments 反推:车号 → 发运日签名 → cycle_id
--   2) 手工标注:用户告知某列编组,直接 insert cycle_trains + 多 row membership
--   3) 跟踪:JOIN wagon_container_shipments 算列实际发车次数 / 列内单辆车循环次数
--
-- Idempotent: CREATE TABLE IF NOT EXISTS。
-- Safe: 纯 DDL,不动数据。

-- ── 1. cycle_trains:列定义 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cycle_trains (
    id TEXT PRIMARY KEY,                       -- 'jiusan_margritte_cycle1' 或 hash
    project_id TEXT NOT NULL,                  -- 'jiusan'
    ship_scope TEXT,                           -- '玛格丽特' 或 NULL(跨船列)
    cycle_name TEXT,                           -- 人话名 '玛格丽特 列1'
    cycle_no INTEGER,                          -- 同船多列时的列序号 1/2/3

    -- 调度参数
    period_days REAL,                          -- 实测平均周期 (e.g. 3.0)
    planned_member_count INTEGER,              -- 计划编组规模 (e.g. 50)
    actual_member_count INTEGER,               -- 实测编组规模
    parallel_offset_days INTEGER,              -- 跟同船其他列错开发车天数

    -- 容量参数(箱体角度)
    expected_box_per_dispatch INTEGER,         -- 一次发车带几个箱 (集装箱默认 2 × 车数)
    expected_box_pool_size INTEGER,            -- 此列预期占用箱池规模

    -- 生命周期
    first_dispatch_at TEXT,                    -- 列首次出现日 (YYYY-MM-DD)
    last_dispatch_at TEXT,                     -- 列末次出现日
    total_dispatch_count INTEGER DEFAULT 0,    -- 总循环次数
    status TEXT NOT NULL DEFAULT 'active',     -- active / retired / planned

    -- 识别溯源
    detection_method TEXT,                     -- 'car_no_signature_inferred' / 'manual'
    detection_signature TEXT,                  -- 发运日签名 '2026-05-19,5-21,5-24,5-26,5-30'
    detection_confidence REAL,                 -- 0~1 自动识别置信度

    notes TEXT,
    metadata TEXT,                             -- JSON 扩展字段
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cycle_trains_project_ship
    ON cycle_trains(project_id, ship_scope);
CREATE INDEX IF NOT EXISTS idx_cycle_trains_status
    ON cycle_trains(status);


-- ── 2. cycle_train_membership:车-列关联 ─────────────────────────────
CREATE TABLE IF NOT EXISTS cycle_train_membership (
    id TEXT PRIMARY KEY,                       -- hash(cycle_id|car_no)
    cycle_id TEXT NOT NULL,                    -- FK cycle_trains.id
    car_no TEXT NOT NULL,                      -- 车号

    -- 此车在此列的成员行为统计
    joined_at TEXT NOT NULL,                   -- 首次在此 cycle 发运日 YYYY-MM-DD
    left_at TEXT,                              -- 末次发运日(NULL = 仍在用)
    dispatch_count INTEGER NOT NULL DEFAULT 1, -- 在此 cycle 累计循环次数
    last_dispatch_at TEXT,                     -- 最近一次发运
    avg_personal_period_days REAL,             -- 本车在此列的平均循环周期(实测)

    -- 状态
    member_status TEXT NOT NULL DEFAULT 'active', -- active / left
    role TEXT,                                 -- core(核心 50 辆)/ swing(机动补) / NULL

    -- 来源
    source TEXT,                               -- 'auto_inferred_from_95306' / 'manual_seed'
    source_note TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cycle_id, car_no),
    FOREIGN KEY(cycle_id) REFERENCES cycle_trains(id)
);

CREATE INDEX IF NOT EXISTS idx_cycle_member_car
    ON cycle_train_membership(car_no);
CREATE INDEX IF NOT EXISTS idx_cycle_member_cycle
    ON cycle_train_membership(cycle_id);


-- ── 3. wagon_container_shipments 反向关联(可选)──────────────────────
-- 为 wagon_container_shipments 加 cycle_id 字段,这样 box-event 直接知道
-- 自己属于哪个循环列。允许 NULL,即未识别归属时不强制。
ALTER TABLE wagon_container_shipments ADD COLUMN cycle_id TEXT;
CREATE INDEX IF NOT EXISTS idx_wcs_cycle_id
    ON wagon_container_shipments(cycle_id);
