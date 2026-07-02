-- R82: contract-fee structures + container ticket fields
-- Date: 2026-07-02
-- Scope:
--   1) wagon_container_shipments 补票面国铁费/原始明细/作业道线
--   2) 合同条款按 route / fee item 结构化落库
--   3) 地铁费按作业道线独立费率落库
--   4) 收入侧业务确认重量独立落库

ALTER TABLE wagon_container_shipments ADD COLUMN freight_fee REAL NOT NULL DEFAULT 0;
ALTER TABLE wagon_container_shipments ADD COLUMN detail_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE wagon_container_shipments ADD COLUMN loading_line TEXT;

CREATE TABLE IF NOT EXISTS contract_fee_terms (
    id TEXT PRIMARY KEY,
    contract_ref TEXT NOT NULL,
    project_id TEXT NOT NULL,
    route_code TEXT,
    fee_code TEXT NOT NULL,
    fee_name TEXT NOT NULL,
    charge_side TEXT NOT NULL,
    pricing_basis TEXT NOT NULL,
    pricing_unit TEXT NOT NULL,
    default_rate REAL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    tax_rate REAL,
    counterparty TEXT,
    settle_party TEXT,
    evidence_path TEXT,
    evidence_locator TEXT,
    effective_from TEXT,
    effective_to TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    UNIQUE(contract_ref, project_id, route_code, fee_code, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_contract_fee_terms_project
    ON contract_fee_terms(project_id, route_code, fee_code, enabled);

CREATE TABLE IF NOT EXISTS contract_line_rates (
    id TEXT PRIMARY KEY,
    contract_fee_term_id TEXT,
    contract_ref TEXT NOT NULL,
    project_id TEXT NOT NULL,
    route_code TEXT,
    fee_code TEXT NOT NULL,
    line_name TEXT NOT NULL,
    rate REAL NOT NULL,
    pricing_unit TEXT NOT NULL,
    tax_rate REAL,
    evidence_path TEXT,
    evidence_locator TEXT,
    effective_from TEXT,
    effective_to TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(contract_fee_term_id) REFERENCES contract_fee_terms(id),
    UNIQUE(project_id, route_code, fee_code, line_name, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_contract_line_rates_lookup
    ON contract_line_rates(project_id, route_code, fee_code, line_name, enabled);

CREATE TABLE IF NOT EXISTS shipment_weight_confirmation (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    ship_name TEXT NOT NULL,
    release_batch_id TEXT,
    route_code TEXT,
    weight_type TEXT NOT NULL,
    confirmed_weight REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT 'ton',
    confirmed_date TEXT,
    confirmed_by TEXT,
    evidence_path TEXT,
    evidence_locator TEXT,
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(release_batch_id) REFERENCES release_batches(id),
    UNIQUE(project_id, ship_name, release_batch_id, route_code, weight_type)
);
CREATE INDEX IF NOT EXISTS idx_weight_confirmation_lookup
    ON shipment_weight_confirmation(project_id, ship_name, route_code, weight_type);
