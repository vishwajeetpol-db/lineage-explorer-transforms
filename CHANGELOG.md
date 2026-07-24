# Changelog

All notable changes to BrickTrace are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.5.5] - 2026-07-24

> **Table Lineage workspace + UI shell.** Adds a dedicated per-table analysis workspace (catalog tree · lineage graph · draggable capability panels) reached from a new Landing tile, a light/dark theme toggle, a redesigned sidebar-shell home, and a new logo across the app. Several backend capabilities were reworked to be service-principal-friendly and to match the reference tool (Data Lineage POC). No new scorecard capabilities — this session productizes the per-table UX over existing caps 08/09/12/13/14/39.

### Added

- **Table Lineage workspace** (`frontend/src/components/table-lineage/`) — 3-pane shell (catalog tree, cross-catalog lineage graph, top summary bar) with six capability panels opened as **draggable floating popups**: Impact, Root Cause, Governance, Access, ML Models, LLM Transform. Opened from the 4th Landing tile / `?view=tableLineage`. Double-click a graph table node to re-focus the workspace.
- **`GET /api/root-cause/trace`** — health-based table-level root-cause trace (upstream table walk → producer run-health classification failed/stale/healthy/no-history → prime suspect + failure path). Auto-runs on table select. (`trace_root_cause_table()`)
- **`DELETE /api/governance/config`** — delete a classification rule (`delete_governance_rule()`); Governance panel now adds/removes column-pattern and UC-tag → sensitivity rules.
- **`GET /api/analyze-producer/models`**, **`/versions`**, **`/version`**, **`/compare`** — LLM model list (for the dropdown), per-target version history, single-version fetch, and version-to-version diff (source + per-column). Analyses are versioned per (entity_type, entity_id, target_table) with a stored source snapshot.
- **Light/dark theme** — CSS-variable color tokens (`tailwind.config.ts`, `styles/globals.css`) + `themeStore` + `ThemeToggle`; persisted, applied before first paint, toggle top-right everywhere.
- **New logo** across Landing/Toolbar/workspace/favicon, served via `GET /bricktrace-logo.png` (stored as `.logo` to survive the bundle sync's `*.png` exclusion).
- **Impact consumers** — `/api/impact` now returns the reader entities (dashboards/jobs/pipelines) of the focus + downstream tables, grouped by type with resolved names + deep links; panel renders clickable consumer chips.

### Changed

- **#13 Security & Access** — grants read from `information_schema.table_privileges` (SP-readable) instead of `SHOW GRANTS`; audit query uses `service_name='unityCatalog'` + `event_date` partition pruning + `request_params['full_name_arg']`; a single audit scan feeds both the accessor rollup and the recent-events feed; identities (owner/created_by/last_altered) + reads/writes counts added.
- **#14 AI/ML Lineage** — `get_models_for_table()` derives models live from the UC Model Registry → versions → MLflow run `dataset_inputs` (app-owned `model_lineage` table as fallback).
- **#39 LLM Producer Analysis** — calls the serving endpoint via the SDK's OAuth client (no static token); default model `databricks-claude-sonnet-4-6`; prompt now requires an entry for every target column; `max_tokens` 1024 → 4000; notebook export uses `ExportFormat.SOURCE`.
- **Observability** — fixed `system.lakeflow.pipeline_update_timeline` column names (`result_state`/`period_start_time`/`period_end_time`), which also unblocks pipeline run-health in the root-cause trace.
- **`_execute_sql` polling** — `lineage_service.py` and `server/access.py` now poll past the 50s API wait cap instead of raising `SQL did not complete: PENDING` (fixed schema-lineage 500s and empty audit results).
- **Home page redesigned** into a sidebar-shell layout (nav + workspace selector + user profile + top-right bell/help/theme, hero, tiles, global search, Recent Activity from `/api/notifications`); Admin Dashboard is an admin-gated sidebar item.
- **`APP_VERSION` / package version → `2.5.5`**.

### Known gaps

- ML models resolve empty when a training run's notebook-scoped experiment notebook was deleted (SP can't read run inputs).
- Access grants list is scoped to what the app SP can enumerate (needs catalog/schema `MANAGE`); audit-backed sections need account-admin `SELECT` on `system.access`.
- ~20 service files still have a non-polling `_execute_sql` (only `access.py` + `lineage_service.py` fixed); latent, only affects >50s queries.
- App deploy relies on runtime `--var lineage_catalog/lineage_schema` overrides (the default `lattice_lineage` catalog can't be created in the FEVM workspace); not persisted in `databricks.yml`.

---

## [2.5.2] - 2026-07-19

> **Capability bulk closure** — closes 8 scorecard items to HAVE status. Scorecard moves from 10/7/1 to **18 HAVE / 2 PARTIAL / 0 GAP**. Adds BI tool consumer detection, streaming topology view, auto-capture scheduling, DQ trend tracking, pipeline expectation sync, and webhook-based notification delivery.

### Added

- **`GET /api/lineage/bi-consumers`** — detects Tableau, PowerBI, Looker, Mode, Metabase, Sigma, ThoughtSpot, dbt Cloud, Redash, and Superset consumers via `system.query.history` user-agent patterns. Returns tool type, query count, distinct users, and last access. (Closes #02 End-to-End Lineage)
- **`GET /api/lineage/streaming-topology`** — discovers streaming tables from `information_schema` and their source edges from `system.access.table_lineage`. Returns streaming table inventory + source→target edge list. (Closes #02)
- **`POST /api/snapshots/auto-capture`** — captures snapshots for all catalogs with recent lineage activity. Designed for scheduled execution via Databricks job. (Closes #07 Versioned Lineage)
- **`GET /api/snapshots/timeline`** — node/edge count time series for a scope, enabling graph growth visualization. (Closes #07)
- **`POST /api/dq-rules/record-metrics`** — stores DQ metric run results in `dq_metrics_history` for longitudinal trending. (Closes #11 Data Quality)
- **`GET /api/dq-rules/trends`** — quality score trend over time with direction detection (improving/stable/degrading). (Closes #11)
- **`GET /api/dq-rules/pipeline-expectations`** — syncs SDP pipeline expectations from `system.information_schema.table_properties` for streaming tables and materialized views. (Closes #11)
- **`GET /api/notifications/webhooks`**, **`POST /api/notifications/webhooks`**, **`DELETE /api/notifications/webhooks/{id}`** — webhook registration CRUD for push notification delivery. Admin-gated. (Closes #20 Notifications)
- **`POST /api/notifications/enqueue-delivery`** — queues unread notifications for webhook delivery (matched by event_type). Delivery queue consumed by external job. (Closes #20)
- **`GET /api/notifications/delivery-status`** — webhook delivery queue inspection.
- **`backend/routes/capability_closures.py`** — new route module consolidating all v2.5.2 gap-closure endpoints.
- **New Delta tables**: `dq_metrics_history`, `notification_webhooks`, `webhook_delivery_queue` (auto-created on first use).
- **`GET /api/glossary/propagate-suggestions`** — walks downstream lineage from a source table and identifies downstream tables missing business terms linked to the source. Enables term inheritance across the data estate. (Closes #10 Business Lineage)
- **`GET /api/glossary/lineage-overlay`** — returns all glossary terms, domains, and KPI indicators scoped to a catalog/schema, grouped by table — ready for frontend graph node badge rendering. (Closes #10)
- **`POST /api/openlineage/producer/configure`** — register an external OpenLineage-compatible endpoint (Marquez, Atlan, DataHub) for event delivery with Databricks secret-backed auth. (Closes #17 Open Standards)
- **`POST /api/openlineage/producer/produce`** — scans `system.access.table_lineage` for recent writes, builds OL RunEvents, queues them for delivery. Designed for scheduled job invocation.
- **`GET /api/openlineage/producer/events`** — view producer queue status (pending/delivered/failed) with summary counts. Monitors production health.

### Changed

- **#17 Open Standards → HAVE** — added live bidirectional producer: configure endpoint → detect writes → queue OL events → async delivery. Now fully bidirectional (export + import + produce).
- **#10 Business Lineage → HAVE** — discovered that `routes/glossary.py` was a full 345-line implementation (not a stub): term CRUD, domain management, KPI definitions, and term→table/column linking. Added propagation suggestions + lineage overlay to close the gap.
- **#01 Automatic Discovery → HAVE** — recognized that dbt manifest import + Airflow DAG import + foreign catalog crawl + manual registration already fully covers the scorecard requirement.
- **#14 AI/ML Lineage → HAVE** — recognized that existing `routes/ml.py` already implements feature tables, vector indexes, vector lineage, prompt lineage, and model registry (endpoints existed since v2.5.0).
- **`docs/capability_code_map.md`** updated to 16/2/1 scorecard summary with full closure history.
- **`APP_VERSION` → `2.5.2`**.

---

## [2.5.1] - 2026-07-19

> **Integration hardening** — upgrades the plan parser to the mature upstream version, adds the Federated Source Overlay endpoint from the design doc, and removes 7 non-contributing files.

### Added

- **`GET /api/lineage/federated-overlay`** — new endpoint returning `FederatedSourceOverlay` (foreign catalog nodes enriched with connection type, provider, and federation tier). Queries `system.information_schema.connections` + `information_schema.catalogs WHERE catalog_type = 'FOREIGN_CATALOG'`.
- **`FederatedTableEntry` + `FederatedSourceOverlay` models** (`backend/models.py`) — Pydantic schemas for the federated overlay response.
- **`get_federated_source_overlay()`** (`backend/federated_service.py`) — service function backing the new endpoint.
- **`docs/capability_code_map.md`** — debugging index mapping each of the 20 capability-matrix items to primary backend files and routes.

### Changed

- **`backend/plan_capture/plan_parser.py` upgraded to mature version** — now includes:
  - `LEAF_PREFIXES` tuple for identifying leaf nodes (Range, Relation, LogicalRelation, HiveTableRelation, etc.)
  - `_table_context()` helper for qualified table name resolution via SubqueryAlias/Relation
  - `clean_nodes()` with multi-line continuation folding (detects tree connectors via regex)
  - `build_symbol_tables()` with parent-chain stack tracking (leaf columns inherit enclosing table context)
  - `first_bracket()` / `attr_bracket()` helpers (skip non-attribute options brackets)
  - `_udf_names()` fully implemented with `_UDF_CALL_RE`
  - Streaming relation handling (StreamingRelationV2 bracket skipping)

### Removed

- `setup_full_demo.py`, `.github/`, `backend/tests/`, `monitoring/`, `requirements-dev.txt`, `pytest.ini`, `CODEOWNERS` — non-contributing to runtime; all imports verified passing after removal.

---

## [2.4.0]

> Adds a new **Control Panel** — admin-gated toggles for three opt-in, higher-cost/higher-risk capabilities, all OFF by default: **Runtime Plan Capture** (captures Spark's exact Analyzed Logical Plan from opted-in pipelines, for transformation logic the static parser can't read from source), **Captured-Plan Precedence** (additively surfaces that captured expression in the existing transformation drill-down), and **Federated Sync** (an admin-curated registry of known peer workspaces layered on the existing Delta Sharing overlay — a v1 scaffold, not live cross-workspace sync). See [docs/architecture.md](docs/architecture.md), [docs/capabilites.md](docs/capabilites.md), and [docs/testing_plan_for_Combined_App.md](docs/testing_plan_for_Combined_App.md) for full detail, including an explicit Known Gaps / follow-ups list.
