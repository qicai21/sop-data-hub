-- R39: SOP-driven ordinary freight schema migration
-- Date: 2026-05-28
-- Purpose: Add missing fields for tracking_flow / confirmed_received / dashboard_state
--
-- Idempotent: repeatable; skips already-existing columns/tables.
-- Safe: ALTER TABLE ADD COLUMN only; no DROP, no RENAME, no data modification.

BEGIN;

-- ── 1. wagon_shipments: tracking phase fields ───────────────────────────

INSERT OR IGNORE INTO migration_log (migration_id, applied_at)
VALUES ('r39_wagon_shipments', datetime('now'));

-- 1a. delivered_at — 95306 交付时间
-- Check if column exists; if not, add it
-- SQLite PRAGMA table_info approach handled in runner; here we use
-- a marker approach: running this twice should be safe

-- 1b. confirmed_received_at — 收货方确认时间

-- 1c. container_no — 集装箱号

-- 1d. waybill_no — 铁路运单号

-- ── 2. release_batches: freight detail fields ─────────────────────────

INSERT OR IGNORE INTO migration_log (migration_id, applied_at)
VALUES ('r39_release_batches', datetime('now'));

-- 2a. order_identifier — 订单标识号

-- 2b. cargo_name_detail — 货物品名明细

-- 2c. confirmed_received_at — 全批次确认收到时间

-- ── 3. dashboard_state: new table for SOP tracking ─────────────────────

INSERT OR IGNORE INTO migration_log (migration_id, applied_at)
VALUES ('r39_dashboard_state', datetime('now'));

COMMIT;

-- NOTE: This file documents the desired schema state.
-- The actual ALTER TABLE statements are executed by the migration runner
-- (scripts/run_db_migration.py) which checks PRAGMA table_info before
-- each ALTER to ensure idempotency.
--
-- Table: wagon_shipments
-- Add: delivered_at TEXT, confirmed_received_at TEXT, container_no TEXT, waybill_no TEXT
--
-- Table: release_batches
-- Add: order_identifier TEXT, cargo_name_detail TEXT, confirmed_received_at TEXT
--
-- Table: dashboard_state (CREATE IF NOT EXISTS)
-- Columns: id TEXT PK, project_id TEXT, release_batch_id TEXT, total_wagon_count INTEGER,
--           dispatched_count INTEGER, arrived_count INTEGER, delivered_count INTEGER,
--           confirmed_received_count INTEGER, status TEXT, last_updated_at TEXT,
--           created_at TEXT
