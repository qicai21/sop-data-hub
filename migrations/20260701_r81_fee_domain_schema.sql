-- R81: fee-domain schema
-- Date: 2026-07-01
-- Scope:
--   1) 费用事实层 fee_*
--   2) 费用归属例外层 fee_allocation_override
--   3) 单证层 doc_*
--   4) 导入与审计层 import_job / import_job_item / audit_log
--   5) 为 billing_reconciliation / billing_invoice / billing_settlement
--      补最小衔接字段
--
-- Notes:
--   - 不迁移旧 billing_cost_item 数据
--   - 不删除任何旧 billing_* 表
--   - CREATE TABLE IF NOT EXISTS 天然幂等
--   - ALTER TABLE ADD COLUMN 由 runner 预查后决定是否执行

CREATE TABLE IF NOT EXISTS fee_item_catalog (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    fee_code TEXT NOT NULL,
    fee_name TEXT NOT NULL,
    charge_side TEXT NOT NULL,               -- income / cost / passthrough
    pricing_unit TEXT NOT NULL,              -- ton / car / box / train / batch / day
    default_rate REAL,
    tax_rate REAL,
    document_flow_type TEXT,
    contract_ref TEXT,
    contract_path TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    UNIQUE(project_id, fee_code)
);
CREATE INDEX IF NOT EXISTS idx_fee_item_catalog_project
    ON fee_item_catalog(project_id);

CREATE TABLE IF NOT EXISTS fee_batch (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    batch_type TEXT NOT NULL,                -- dispatch_train / container_dispatch / delivered_train / ship_closeout / monthly_summary / manual_summary
    mode TEXT NOT NULL DEFAULT 'bulk',       -- bulk / container / mixed
    route_code TEXT,
    release_batch_id TEXT,
    ship_name TEXT,
    location_code TEXT,
    yard_line TEXT,
    event_date TEXT,
    period_start TEXT,
    period_end TEXT,
    wagon_count INTEGER,
    container_count INTEGER,
    weight_basis TEXT,                       -- marked_weight / settlement_weight / billing_weight / manual
    total_weight REAL,
    recognition_scope TEXT,                  -- train / container_batch / ship / month / manual
    recognition_key TEXT,
    status TEXT NOT NULL DEFAULT 'open',     -- open / eligible / cost_generated / income_generated / reconciled / settled / void
    source_mode TEXT NOT NULL DEFAULT 'system_generated',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(release_batch_id) REFERENCES release_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_fee_batch_project_type
    ON fee_batch(project_id, batch_type, recognition_scope);
CREATE INDEX IF NOT EXISTS idx_fee_batch_release_batch
    ON fee_batch(release_batch_id);

CREATE TABLE IF NOT EXISTS fee_batch_member (
    id TEXT PRIMARY KEY,
    fee_batch_id TEXT NOT NULL,
    unit_type TEXT NOT NULL,                 -- wagon / container
    wagon_shipment_id TEXT,
    wagon_container_shipment_id TEXT,
    ydid TEXT,
    car_no TEXT,
    box_no TEXT,
    marked_weight REAL,
    settlement_weight REAL,
    billing_weight REAL,
    ticketed_at TEXT,
    departed_at TEXT,
    arrived_at TEXT,
    delivered_at TEXT,
    source_mode TEXT NOT NULL DEFAULT 'system_generated',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(fee_batch_id) REFERENCES fee_batch(id)
);
CREATE INDEX IF NOT EXISTS idx_fee_batch_member_batch
    ON fee_batch_member(fee_batch_id);
CREATE INDEX IF NOT EXISTS idx_fee_batch_member_car
    ON fee_batch_member(car_no);
CREATE INDEX IF NOT EXISTS idx_fee_batch_member_box
    ON fee_batch_member(box_no);
CREATE INDEX IF NOT EXISTS idx_fee_batch_member_ydid
    ON fee_batch_member(ydid);

CREATE TABLE IF NOT EXISTS fee_record (
    id TEXT PRIMARY KEY,
    fee_batch_id TEXT NOT NULL,
    fee_item_id TEXT NOT NULL,
    fee_code TEXT NOT NULL,
    fee_name_snapshot TEXT NOT NULL,
    charge_side TEXT NOT NULL,
    counterparty TEXT,
    settle_party TEXT,
    price REAL,
    qty REAL,
    qty_unit TEXT,                           -- ton / car / box / batch / day
    amount REAL,
    pricing_basis TEXT,                      -- marked_weight / settlement_weight / billing_weight / wagon_count / container_count / train_count / manual
    pricing_basis_value REAL,
    recognition_scope TEXT,                  -- train / container_batch / ship / month / manual
    recognition_key TEXT,
    document_flow_type TEXT,
    status TEXT NOT NULL DEFAULT 'generated', -- generated / confirmed / reconciled / invoiced / settled / void
    reconciliation_id TEXT,
    source_mode TEXT NOT NULL DEFAULT 'system_generated',
    source_ref TEXT,
    version_no INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(fee_batch_id) REFERENCES fee_batch(id),
    FOREIGN KEY(fee_item_id) REFERENCES fee_item_catalog(id)
);
CREATE INDEX IF NOT EXISTS idx_fee_record_batch
    ON fee_record(fee_batch_id);
CREATE INDEX IF NOT EXISTS idx_fee_record_item
    ON fee_record(fee_item_id, fee_code);
CREATE INDEX IF NOT EXISTS idx_fee_record_scope
    ON fee_record(recognition_scope, recognition_key);
CREATE INDEX IF NOT EXISTS idx_fee_record_reconciliation
    ON fee_record(reconciliation_id);

CREATE TABLE IF NOT EXISTS fee_allocation_override (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    fee_item_id TEXT,
    fee_code TEXT NOT NULL,
    scope_type TEXT NOT NULL,                -- wagon / container / ydid
    scope_key TEXT,
    car_no TEXT,
    box_no TEXT,
    ydid TEXT,
    from_party TEXT,
    to_party TEXT,
    weight_basis_value REAL,
    reason_code TEXT,
    effective_from TEXT,
    effective_to TEXT,
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(fee_item_id) REFERENCES fee_item_catalog(id)
);
CREATE INDEX IF NOT EXISTS idx_fee_allocation_override_scope
    ON fee_allocation_override(project_id, fee_code, scope_type);

CREATE TABLE IF NOT EXISTS doc_template (
    id TEXT PRIMARY KEY,
    template_code TEXT NOT NULL,
    template_name TEXT NOT NULL,
    doc_type TEXT NOT NULL,                  -- onsite_confirm_sheet / operation_sheet / reconcile_sheet / invoice_attachment
    project_scope TEXT,
    applicable_fee_code TEXT,
    output_format TEXT NOT NULL DEFAULT 'docx', -- docx / pdf / both
    template_path TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    UNIQUE(template_code, project_scope, applicable_fee_code)
);

CREATE TABLE IF NOT EXISTS doc_instance (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fee_batch_id TEXT,
    fee_record_id TEXT,
    doc_type TEXT NOT NULL,
    doc_status TEXT NOT NULL DEFAULT 'generated', -- generated / signed / archived / void
    file_path_docx TEXT,
    file_path_pdf TEXT,
    version_no INTEGER NOT NULL DEFAULT 1,
    generated_at TEXT,
    source_mode TEXT NOT NULL DEFAULT 'system_generated',
    source_ref TEXT,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(template_id) REFERENCES doc_template(id),
    FOREIGN KEY(fee_batch_id) REFERENCES fee_batch(id),
    FOREIGN KEY(fee_record_id) REFERENCES fee_record(id)
);
CREATE INDEX IF NOT EXISTS idx_doc_instance_batch
    ON doc_instance(fee_batch_id);
CREATE INDEX IF NOT EXISTS idx_doc_instance_record
    ON doc_instance(fee_record_id);

CREATE TABLE IF NOT EXISTS doc_instance_signoff (
    id TEXT PRIMARY KEY,
    doc_instance_id TEXT NOT NULL,
    role_type TEXT NOT NULL,                 -- operator_team / entrusting_party / service_party
    party_name TEXT,
    signer_name TEXT,
    signed_at TEXT,
    sign_status TEXT NOT NULL DEFAULT 'pending', -- pending / signed / waived
    source_mode TEXT NOT NULL DEFAULT 'manual_entry',
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT,
    FOREIGN KEY(doc_instance_id) REFERENCES doc_instance(id)
);
CREATE INDEX IF NOT EXISTS idx_doc_instance_signoff_doc
    ON doc_instance_signoff(doc_instance_id);

CREATE TABLE IF NOT EXISTS import_job (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    import_type TEXT NOT NULL,               -- fee_batch / fee_record / document / reconciliation
    source_file_path TEXT,
    source_file_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / failed
    started_at TEXT,
    finished_at TEXT,
    operator TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS import_job_item (
    id TEXT PRIMARY KEY,
    import_job_id TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT,
    source_row_no INTEGER,
    import_status TEXT NOT NULL,             -- inserted / updated / skipped / failed
    message TEXT,
    FOREIGN KEY(import_job_id) REFERENCES import_job(id)
);
CREATE INDEX IF NOT EXISTS idx_import_job_item_job
    ON import_job_item(import_job_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action_type TEXT NOT NULL,               -- create / update / import / backfill / void
    before_snapshot TEXT,
    after_snapshot TEXT,
    operator TEXT,
    acted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity
    ON audit_log(entity_type, entity_id);

ALTER TABLE billing_reconciliation ADD COLUMN recognition_scope TEXT;
ALTER TABLE billing_reconciliation ADD COLUMN recognition_key TEXT;
ALTER TABLE billing_reconciliation ADD COLUMN source_mode TEXT DEFAULT 'manual_entry';
ALTER TABLE billing_reconciliation ADD COLUMN source_ref TEXT;
ALTER TABLE billing_reconciliation ADD COLUMN created_by TEXT;
ALTER TABLE billing_reconciliation ADD COLUMN updated_by TEXT;

ALTER TABLE billing_invoice ADD COLUMN source_mode TEXT DEFAULT 'manual_entry';
ALTER TABLE billing_invoice ADD COLUMN source_ref TEXT;
ALTER TABLE billing_invoice ADD COLUMN created_by TEXT;
ALTER TABLE billing_invoice ADD COLUMN updated_by TEXT;

ALTER TABLE billing_settlement ADD COLUMN source_mode TEXT DEFAULT 'manual_entry';
ALTER TABLE billing_settlement ADD COLUMN source_ref TEXT;
ALTER TABLE billing_settlement ADD COLUMN created_by TEXT;
ALTER TABLE billing_settlement ADD COLUMN updated_by TEXT;
