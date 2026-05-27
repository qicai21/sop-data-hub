# Real SOP Fixture Topology Audit — R1

- Date: 2026-05-25
- Repository: `qicai21/ops-data-hub`
- Branch: `codex/sop-real-sop-topology-audit-20260525`
- Current commit: `0d73716`
- Source repository inspected read-only: `qicai21/prompts_and_reports`
- Source branches inspected: `main`, `jilin-jingang-jinzhou-sop-init-20260521`
- Round: `R1`

## 1) Target project list

1. 中唐特钢
2. 朝阳钢铁
3. 吉林金钢 / 吉林金刚
4. 九三大豆

## 2) Source SOP discovery results

### 2.1 中唐特钢
- Source repo: `qicai21/prompts_and_reports`
- Source branch: `main` (present on both `main` and `jilin-jingang-jinzhou-sop-init-20260521`; no branch-specific divergence found for this file)
- SOP file path: `sops/zhongtangtegang_sop.md`
- File format: Markdown prose with tabular sections and headings
- Direct fixture use: **No**. It is a prose SOP, not a structured fixture.
- Source-doc completeness: **Yes**. It contains project scope, data sources, node definitions, and report targets.
- Equivalent copy already present in `ops-data-hub`: **No direct copy found**
- Proposed fixture path: `tests/fixtures/sops/zhongtang_special_steel_sop.md`
- Missing structured fields for future loader:
  - `sop_nodes`
  - `monitoring` / `monitoring_requirements`
  - explicit `channel`
  - structured `wechat group identifier / group name`
  - structured `input_type`
  - structured `message/document type`
  - explicit `text patterns / document patterns`
  - explicit `target SOP node id`

### 2.2 朝阳钢铁
- Source repo: `qicai21/prompts_and_reports`
- Source branch: `main` (present on both `main` and `jilin-jingang-jinzhou-sop-init-20260521`; no branch-specific divergence found for this file)
- SOP file path: `sops/chaoyangsteel_sop.md`
- File format: Markdown prose with tabular sections and headings
- Direct fixture use: **No**. It is a prose SOP, not a structured fixture.
- Source-doc completeness: **Yes**. It contains project scope, data sources, node definitions, and report artifact constraints.
- Equivalent copy already present in `ops-data-hub`: **No direct copy found**
- Proposed fixture path: `tests/fixtures/sops/chaoyang_steel_sop.md`
- Missing structured fields for future loader:
  - `sop_nodes`
  - `monitoring` / `monitoring_requirements`
  - explicit `channel`
  - structured `wechat group identifier / group name`
  - structured `input_type`
  - structured `message/document type`
  - explicit `text patterns / document patterns`
  - explicit `target SOP node id`

### 2.3 吉林金钢 / 吉林金刚
- Source repo: `qicai21/prompts_and_reports`
- Source branch: `jilin-jingang-jinzhou-sop-init-20260521`
- SOP file path: `sops/jilin_jingang_jinzhou_sop.md`
- File format: Markdown prose with tabular sections and headings
- Direct fixture use: **No**. It is a prose SOP, not a structured fixture.
- Source-doc completeness: **Yes**. It contains project scope, explicit isolation warnings, data sources, node definitions, and report/upload constraints.
- Equivalent copy already present in `ops-data-hub`: **No direct copy found**
- Proposed fixture path: `tests/fixtures/sops/jilin_jingang_sop.md`
- Missing structured fields for future loader:
  - `sop_nodes`
  - `monitoring` / `monitoring_requirements`
  - explicit `channel`
  - structured `wechat group identifier / group name`
  - structured `input_type`
  - structured `message/document type`
  - explicit `text patterns / document patterns`
  - explicit `target SOP node id`

### 2.4 九三大豆
- Source repo: `qicai21/prompts_and_reports`
- Source branch: `jilin-jingang-jinzhou-sop-init-20260521`
- SOP file path: `sops/jiusan_soybean_sop.md`
- File format: Markdown prose with tabular sections and headings
- Direct fixture use: **No**. It is a prose SOP, not a structured fixture.
- Source-doc completeness: **Partial but usable as source material**. It clearly states the project scope, the fact that it is an initialization SOP, the fixed data sources, and the route/terminology boundaries, but it does not yet define a full structured monitoring plan.
- Equivalent copy already present in `ops-data-hub`: **No direct copy found**
- Proposed fixture path: `tests/fixtures/sops/jiusan_soybean_sop.md`
- Missing structured fields for future loader:
  - `sop_nodes`
  - `monitoring` / `monitoring_requirements`
  - explicit `channel`
  - structured `wechat group identifier / group name`
  - structured `input_type`
  - structured `message/document type`
  - explicit `text patterns / document patterns`
  - explicit `target SOP node id`

## 3) Fixture strategy

- R1 should **not** copy files yet.
- Preferred next fixture layout if the source docs are accepted as stable:
  - `tests/fixtures/sops/zhongtang_special_steel_sop.md`
  - `tests/fixtures/sops/chaoyang_steel_sop.md`
  - `tests/fixtures/sops/jilin_jingang_sop.md`
  - `tests/fixtures/sops/jiusan_soybean_sop.md`
- Loader contract should normalize the prose SOPs into a structured form with at least:
  - `project_id`
  - `project_name`
  - `sop_nodes`
  - `monitoring requirements`
  - `channel`
  - `wechat group identifier or group name`
  - `input_type`
  - `message/document type`
  - `text patterns / document patterns`
  - `target SOP node id`

## 4) Repository evidence for ordinary freight dashboard

Facts found in `ops-data-hub`:
- `src/ops_hub/cli.py` exposes `dispatch-board` and explicitly says it refreshes a dispatch board (`JSON + HTML`).
- `src/ops_hub/data_agent/dispatch_board.py` renders a static dispatch board from the business DB plus an optional read-only 95306 linkage DB.
- `dashboard/dispatch_board_schema.md` documents the dispatch board JSON schema.
- `dashboard/dispatch_board.html` exists as the rendered dashboard artifact.
- `dashboard/dispatch_board_data.example.json` exists as sample board data.
- `src/ops_hub/data_agent/dispatch_board.py` filters `release_batches` from the business DB and uses `active_business_sop_project_tokens()`.
- `dashboard/dispatch_board_schema.md` explicitly states the authoritative board lives under `ops-data-hub/dashboard/`.

Interpretation:
- `ops-data-hub` does document and encode an ordinary freight / dispatch board.
- The board is tied to the business DB and release-batch pipeline, not to the 九三 cycle DB.

## 5) Repository evidence for jiusan soybean cycle dashboard

Facts found in `ops-data-hub`:
- `scripts/jiusan_board_generate.py` generates the 九三大豆 dashboard HTML and JSON.
- `dashboard/jiusan_dashboard.html` exists.
- `dashboard/jiusan_dashboard_data.json` / related sample artifacts exist in the repo history and runtime docs.
- `schema/jiusan_cycle_schema.sql` defines the `jiusan_cycle.db` schema.
- `schema/jiusan_cycle_v3_migration.sql` explicitly says it only extends `jiusan_cycle.db` and does not modify the production DB.
- `reports/2026-05-22_hermes_jiusan_cycle_train_tracking_requirement_report.md` and related reports describe the jiusan tracking pipeline.
- `scripts/jiusan_board_generate.py` reads both `AGENT_DB` and `JIUSAN_DB`, and also references the read-only 95306 SQLite file.

Interpretation:
- `ops-data-hub` clearly documents and encodes a separate 九三大豆 cycle dashboard.
- This dashboard is distinct from the ordinary freight / dispatch board.

## 6) Repository evidence for database / store separation

Facts found in `ops-data-hub`:
- Ordinary freight / dispatch board uses `data/sop_agent.db` as the business DB source.
- Nine-three cycle board uses `data/jiusan_cycle.db` as its own working DB.
- `scripts/jiusan_board_generate.py` also reads the read-only 95306 store from `rail95306-sync/runtime/95306_collection.sqlite3`.
- `schema/jiusan_cycle_v3_migration.sql` explicitly says: `不修改生产库，只扩展 jiusan_cycle.db`.
- `dashboard/dispatch_board_schema.md` names `sop-data-hub/data/sop_agent.db` as the source DB for the dispatch board.

Interpretation:
- `ops-data-hub` documents a separation between the dispatch-board store and the jiusan cycle store.
- The repository also separates each of those from the read-only 95306 linkage store.

## 7) Test / validation status

- No code or test files were modified in this round.
- Tests were therefore optional under the order.
- No pytest run was required for R1 audit.

## 8) Recommended next order

Implement a minimal real-SOP fixture pipeline for the four discovered docs:
1. copy the four stable SOP sources into `tests/fixtures/sops/`;
2. add or extend the loader so it extracts the structured contract fields listed above;
3. then wire the fixture-backed topology into the monitoring plan compiler.

Do **not** invent any SOP content; use only the four real documents discovered above.
