# Capability → Code Map

> Debugging index for BrickTrace. Maps the **20 scorecard capabilities** (from capability-matrix PDF)
> AND the **full feature inventory** (48 routes/subsystems) to code anchors.
> Status: **HAVE** / **PARTIAL** / **GAP** — with closure notes for each.
>
> **v2.5.6:** Column Transformation Lineage now uses a **unified precedence
> resolver** (captured Spark plan → CDC spec → stored LLM → fresh LLM) with a
> **cross-source version history + compare** (diff a captured plan against an
> LLM deduction). Captured-plan tables are configurable so the reader can point
> at the offline `lineage_capture` project's schema. See Part F.
>
> **v2.5.5:** added the **Table Lineage workspace** — a dedicated
> sidebar-shell home + 3-pane workspace (catalog tree · lineage graph · draggable
> capability panels) that surfaces Impact / Root Cause / Governance / Access / ML /
> LLM-Transform per selected table. Several backend capabilities were reworked to be
> service-principal-friendly and to match the reference tool (see **Part F**). Also
> shipped: light/dark theme toggle, new logo across the app. Frontend components live
> under `frontend/src/components/table-lineage/`; see Part F for the full list.

---

## Part A — Scorecard Capabilities (20)

| # | Capability | Status | Primary Files | Route(s) | Notes |
|---|-----------|--------|---------------|----------|-------|
| 01 | Automatic Discovery | HAVE | `routes/external_sources.py`, `federated_service.py` | `POST /api/external/dbt/import`, `/api/external/airflow/import`, `/api/external/discover`, `GET /api/lineage/federated-overlay` | dbt manifest + Airflow DAG import + manual registration + foreign catalog crawl |
| 02 | End-to-End Lineage | HAVE | `routes/external_sources.py`, `routes/capability_closures.py` | `/api/lineage/bi-consumers`, `/api/lineage/streaming-topology` | BI tool detection via query history user-agents + streaming table topology + dbt/Airflow + federated + Delta Sharing |
| 03 | Column-Level Lineage | HAVE | `sublineage/backtrack.py`, `transform_service.py` | `GET /api/sublineage` | — |
| 04 | Transformation Logic | HAVE | `transform_service.py`, `build_service.py`, `plan_capture/` | `GET /api/transform`, `POST /api/analyze-producer` | — |
| 05 | Multi-Platform | HAVE | `routes/external_sources.py` (ol-bridge section), `federated_service.py` | `POST /api/external/ol-bridge/register`, `POST /api/external/ol-bridge/ingest/{source_id}`, `GET /api/external/ol-bridge/sources`, `GET /api/external/ol-bridge/events`, `GET /api/lineage/federated-overlay` | OL producer bridge: external OL-emitting platforms (Snowflake Horizon, BigQuery via OL proxy, Spark + openlineage-spark, Flink, dbt Cloud, Airflow 2.7+) register once and push standard RunEvents to a dedicated receive URL; edges stored in `external_ol_bridge_events` and surfaced in the lineage graph |
| 06 | Near Real-Time | HAVE | `lineage_service.py` (TTL cache 8h) | all `/api/lineage/*` | — |
| 07 | Versioned Lineage | HAVE | `routes/graph_snapshots.py`, `routes/capability_closures.py` | `POST /api/snapshots/capture`, `GET /api/snapshots/diff`, `POST /api/snapshots/auto-capture`, `GET /api/snapshots/timeline` | On-demand + scheduled capture, diff viewer, growth timeline |
| 08 | Impact Analysis | HAVE | `routes/impact.py`, `server/entities.py`, `components/table-lineage/ImpactPanel.tsx` | `GET /api/impact` | v2.5.5: `_consumers()` now returns the entities (dashboards/jobs/pipelines) that READ the focus + downstream tables, grouped by type + resolved to display names & deep links (via `resolve_entities`); panel renders clickable consumer chips |
| 09 | Root Cause Analysis | HAVE | `server/root_cause.py`, `routes/root_cause.py`, `components/table-lineage/RootCausePanel.tsx` | `POST /api/root-cause/analyze`, `GET /api/root-cause/trace` | v2.5.5: added `trace_root_cause_table()` + `GET /api/root-cause/trace` — health-based table-level trace (upstream table walk → producer run health failed/stale/healthy/no-history → prime suspect + failure path), auto-run on table select. Column-level `analyze` (BFS + failed runs + DQ) unchanged |
| 10 | Business Lineage | HAVE| `routes/glossary.py` | `/api/glossary/terms`, `/api/glossary/domains`, `/api/glossary/kpis`, `/api/glossary/link`, `/api/glossary/propagate-suggestions`, `/api/glossary/lineage-overlay` | Full glossary: term CRUD + domains + KPIs + term→table/column linking + downstream propagation suggestions + lineage graph overlay |
| 11 | Data Quality | HAVE | `routes/dq.py`, `routes/capability_closures.py` | `/api/dq-rules/metrics`, `/api/dq-rules/trends`, `/api/dq-rules/pipeline-expectations`, `/api/dq-rules/propagation` | Live metrics + trend history + pipeline expectation sync + upstream propagation |
| 12 | Governance | HAVE | `routes/governance.py`, `server/governance.py`, `components/table-lineage/GovernancePanel.tsx` | `GET /api/governance`, `GET\|POST\|DELETE /api/governance/config` | v2.5.5: response adds `table_type`/`created_by`/`last_altered`; new `DELETE /api/governance/config` + `delete_governance_rule()`; panel manages classification rules (column-pattern + UC-tag → sensitivity), admin-gated |
| 13 | Security & Access | HAVE | `routes/access.py`, `server/access.py`, `components/table-lineage/AccessPanel.tsx` | `GET /api/access` | v2.5.5: grants now via `information_schema.table_privileges` (SP-readable, not `SHOW GRANTS`); audit query uses `service_name='unityCatalog'` + `event_date` partition + `request_params['full_name_arg']`; single audit scan feeds both the accessor rollup and the recent-events feed; identities (owner/created_by/last_altered) + reads/writes counts; `_execute_sql` now polls past the 50s cap |
| 14 | AI/ML Lineage | HAVE | `routes/ml.py`, `server/ml.py`, `components/table-lineage/MLModelsPanel.tsx` | `/api/ml/endpoints`, `/api/ml/models-for-table`, `/api/ml/feature-tables`, `/api/ml/vector-indexes`, `/api/ml/vector-lineage`, `/api/ml/prompt-lineage` | v2.5.5: `get_models_for_table()` now derives models LIVE from the UC Model Registry → model versions → MLflow run `dataset_inputs` (with the app-owned `model_lineage` table as fallback); panel shows serving-endpoint status per model. Serving endpoints + feature store + vector + prompt + registry unchanged |
| 15 | Interactive Viz | HAVE | `frontend/dist/` (React+ELK.js) | static serving | — |
| 16 | Search & Discovery | HAVE | `routes/search.py`, `routes/discovery.py` | `/api/search`, `/api/discover/*` | — |
| 17 | Open Standards | HAVE| `routes/openlineage.py` | `GET /api/export/openlineage`, `POST /api/import/openlineage`, `POST /api/openlineage/producer/produce`, `GET /api/openlineage/producer/events` | Full bidirectional: export + ingest + live producer (detects writes → queues OL events → configured endpoints deliver) |
| 18 | Scalability | HAVE | `routes/scalability.py`, `cache_service.py`, `perf_patches.py`, `main.py` | `GET /api/scalability/graph`, `GET /api/scalability/cache/stats`, `POST /api/scalability/cache/invalidate`, `GET /api/scalability/health` | Delta-backed distributed cache (shared across replicas, no Redis needed) + BFS cursor-paginated graph (removes 400-node cap) + parallel catalog enumeration + 8-worker pool |
| 19 | Observability | HAVE | `routes/observability.py` | `GET /api/observability` | — |
| 20 | Notifications | HAVE | `routes/notifications.py`, `routes/capability_closures.py` | `/api/notifications/scan`, `/api/notifications/webhooks`, `/api/notifications/enqueue-delivery` | Detection (schema/DQ/sensitive) + alert rules + webhook registration + delivery queue |

**Summary**: 18 HAVE, 2 PARTIAL, 0 GAP (v2.5.4)

> **Honesty update (2026-07-22):** All 20 capabilities have backend APIs. 18 have a
> complete product path (backend + routed UI in shipped dist). 2 remain PARTIAL:
> - **#05 Multi-Platform (Federated Sync)**: Scaffold-level; peer registry + overlay, not live cross-workspace API calls.
> - **#19 Observability**: API returns metrics; dedicated monitoring dashboard not yet surfaced in App UI.
>
> Edge-case guards (`backend/edge_case_guards.py`) now provide runtime diagnostics for
> system-table availability (C1), partial-catalog-access (C2), graph truncation (C4),
> and SCD/CDC detection (C16).

---

## Part B — Full Feature Inventory (beyond scorecard)

| # | Feature | Primary Files | Route(s) |
|---|---------|---------------|----------|
| 21 | Landing Explorer / Browse | `routes/browse.py`, `lineage_service.py` | `GET /api/catalogs`, `/api/schemas`, `/api/tables` |
| 22 | Deep-link Routing | `frontend/src/hooks/useRouter.ts` | `?table=`, `?view=`, `?admin=`, `?controlPanel=` |
| 23 | Three View Modes + Depth | `frontend/src/components/graph/` | client-side (Tables/Pipelines/Full + hop slider) |
| 24 | In-graph Tools (search, fit, drag) | `frontend/src/components/graph/` | client-side Cmd+K, fitView, drag |
| 25 | Cross-Schema/Catalog Enrichment | `lineage_service.py` | included in `/api/lineage` response |
| 26 | Expression-Level Transform | `transform_service.py`, `transformation_lineage/` | `GET /api/transform/{table}/{column}` |
| 27 | Transform Freshness + Builder | `build_service.py` (`get_pipeline_notebook_path()`, `submit_build_job()`, `is_build_configured()`) | `POST /api/transform/build`, `GET /api/transform/status/{run_id}`, `GET /api/transform/build-configured` | v2.6.0: lazy-cached path resolution (env → App source discovery via `apps.get` → fail-closed); `/build-configured` returns `notebook_path`; `_reset_pipeline_notebook_path_cache()` for test isolation |
| 28 | Transform Diagnostics | `transform_service.py` | `GET /api/transform/diagnose` |
| 29 | Delta Sharing Overlay | `lineage_service.py` → `get_sharing_overlay()` | included in lineage response |
| 30 | Serverless Cost on Nodes | `cost_service.py` | `GET /api/cost/{entity}` |
| 31 | Admin Ops Dashboard | `routes/admin.py` | `GET /api/admin/metrics`, `/api/cache/*` |
| 32 | Excel Export + Preview | `routes/export.py` | `GET /api/lineage/export` |
| 33 | Request Coalescing + Cache | `main.py` (middleware) | infra (no dedicated route) |
| 34 | Control Panel (flags) | `feature_flags.py`, `routes/control_panel.py` | `GET\|POST /api/control-panel/flags/*` |
| 35 | Runtime Plan Capture | `plan_capture/capture.py` | write path (pipeline notebooks only) |
| 36 | Captured-Plan Precedence | `plan_capture_service.py`, `sublineage/backtrack.py` | `GET /api/transform/captured-expression` |
| 37 | Federated Sync (peers) | `federated_sync.py` | `GET\|POST /api/control-panel/federated/*` |
| 38 | Federated Source Overlay | `federated_service.py` | `GET /api/lineage/federated-overlay` |
| 39 | Column Transformation Lineage (LLM + captured plan) | `routes/lineage.py` (analyze_router), `server/producer_source.py`, `server/llm.py`, `server/analysis_store.py`, `plan_capture_service.py`, `components/table-lineage/ColumnTransformationPanel.tsx` | `POST /api/column-transformations`, `/api/column-transformations/versions`, `/api/column-transformations/compare`, `POST /api/analyze-producer`, `GET /api/analyze-producer/models` | v2.5.6: **unified precedence resolver** (POC-style) — captured Spark plan → captured CDC spec → stored LLM version (stale-flagged) → fresh LLM. `resolve_column_transformations()` picks best-source-first; `list_all_versions()` merges captured-plan + LLM versions (each with a `ref` like `plan_capture:2`/`llm:6`); `compare_transformation_versions()` diffs ANY two refs incl. **cross-source** (captured plan vs LLM). Captured-plan tables now configurable (`CAPTURED_PLANS_TABLE`/`CAPTURED_CDC_TABLE`) so the reader can point at the offline `lineage_capture` project's schema; NULL versions handled via `coalesce`. v2.5.5 base: SDK OAuth call, model dropdown, full-column prompt, per-(entity,target) versioning + source snapshot |
| 40 | Pipeline Capture Installer | `routes/pipeline_installer.py` | `POST /api/pipeline/install-capture` |
| 41 | SCD/CDC Spec Viewer | `routes/diagnostics.py` | `GET /api/diagnostics/scd` |
| 42 | Schema-Change Detector | `routes/diagnostics.py` | `GET /api/diagnostics/schema-changes` |
| 43 | Column Profiling Overlay | `routes/diagnostics.py` | `GET /api/diagnostics/profile` |
| 44 | Billing per Feature Flag | `routes/diagnostics.py` | `GET /api/diagnostics/billing/{flag_id}` |
| 45 | Federated Trust Handshake | `routes/diagnostics.py` | `GET\|POST /api/diagnostics/federated/*` |
| 46 | PII/Sensitive Column Finder | `routes/discovery.py` | `GET /api/discover/sensitive` |
| 47 | Orphan Table Detector | `routes/discovery.py` | `GET /api/discover/orphans` |
| 48 | Sensitivity Propagation | `routes/governance.py` | included in governance response |

---

## Part C — Key Subsystems (for debugging)

| Subsystem | Entry Point | Notes |
|-----------|------------|-------|
| Plan Capture (write) | `backend/plan_capture/capture.py` | Runs inside pipeline notebooks, writes to `captured_plans` |
| Plan Parser | `backend/plan_capture/plan_parser.py` | Parent-chain tracking, multi-line folding, LEAF_PREFIXES, UDF detection |
| Plan Capture (read) | `backend/plan_capture_service.py` | Flag-gated; `get_captured_expression()` |
| Federated Overlay | `backend/federated_service.py` | `system.information_schema.connections` + foreign catalogs |
| Feature Flags | `backend/feature_flags.py` | Delta-backed toggles; env-var kill switches |
| Transform BFS | `backend/sublineage/backtrack.py` | Column-level upstream walk; captured-plan precedence |
| Build Pipeline | `notebooks/run_pipeline` → `transformation_lineage/` | Serverless job |
| Cost Service | `backend/cost_service.py` | 30-day billing from `system.billing` |
| Sharing Overlay | `backend/lineage_service.py` → `get_sharing_overlay()` | Delta Sharing boundary nodes |
| Control Panel | `backend/feature_flags.py` + `routes/control_panel.py` | Admin toggles UI |
| Root Cause + DQ | `backend/server/root_cause.py` | BFS walk → failed runs + DQ rule violations |
| Capability Closures | `backend/routes/capability_closures.py` | BI consumers, streaming topology, auto-snapshots, DQ trends, webhooks |
| OL Bridge | `backend/routes/external_sources.py` (ol-bridge section) | Register → receive URL → external platform pushes OL RunEvents → `external_ol_bridge_events` |
| Distributed Cache | `backend/cache_service.py` | Delta-table-backed shared cache; DeltaCacheService singleton; MERGE upsert + TTL expiry |
| Graph Pagination | `backend/routes/scalability.py` | BFS cursor-pagination (opaque base64 cursors), removes 400-node cap |
| Perf Patches | `backend/perf_patches.py` | Side-effect import: parallel list_all_tables, parallel BFS trace, parallel cost, Delta cache wiring |

---

## Part D — Closure History

### v2.6.x — Business view, precise dataset lineage & AI graph explanation

| Item | Was | Now | How |
|------|-----|-----|-----|
| Non-engineer readability | Single technical graph only | HAVE | Client-side **Business view** (relabel + humanize + plain-English descriptions + hide detail) with a **Data-only / Data + processing** sub-toggle. See **Part H**. |
| Dataset-only lineage accuracy | Collapsing entities cross-producted inputs × outputs → dense mesh for hub tables | HAVE | Backend returns precise per-row `table_edges`; data-only renders those instead of reconstructing. See **Part H3**. |
| Graph explainability | None | HAVE | **AI "Explain this lineage"** lightbulb → `POST /api/lineage/explain` narrates the on-screen graph (summary + steps). See **Part H4**. |

### v2.5.6 — Unified column-transformation precedence + cross-source versioning

| Item | Was | Now | How |
|------|-----|-----|-----|
| #39 Column Transformation | LLM-only panel; captured plans a separate flag-gated surface | HAVE (unified) | `resolve_column_transformations()` walks the POC precedence chain (captured plan → CDC → stored LLM → fresh LLM); `list_all_versions()` + `compare_transformation_versions()` give one version history across both sources and cross-source diff. Panel renamed **Column Transformation Lineage**. |
| Captured-plan source | Read only from app-owned schema (empty in demo) | HAVE | `CAPTURED_PLANS_TABLE`/`CAPTURED_CDC_TABLE` env vars point the reader at the offline `lineage_capture` project's schema (`lineage_explorer`); NULL versions handled. Requires the SP to have `USE SCHEMA` on that schema + the `plan_capture` flag on. |

### v2.5.5 — Table Lineage workspace + UI shell

| Item | Was | Now | How |
|------|-----|-----|-----|
| Per-table UX | APIs only (caps 08/09/12/13/14/39 had no unified UI) | HAVE | New `table-lineage/` workspace: catalog tree + lineage graph + draggable capability panels, opened from a Landing tile. See **Part F**. |
| #08 Impact | API returned owners only | HAVE (fuller) | Added consumer entities (readers) grouped by type + clickable deep links. |
| #09 Root Cause | column+DQ `analyze` only | HAVE (fuller) | Added health-based table-level `GET /api/root-cause/trace`. |
| #13 Access | `SHOW GRANTS` (SP saw nothing) + non-polling audit (PENDING) | HAVE (working for SP) | `table_privileges` grants + POC-style audit + polling `_execute_sql`. |
| #14 AI/ML | app-owned table only (empty) | HAVE (live) | Live derivation from UC registry + MLflow run inputs. |
| #39 LLM Analysis | static-token (unconfigured) + no versioning UI | HAVE | SDK OAuth + model dropdown + versioning + compare. |
| Theme | dark only | HAVE | Light/dark toggle via CSS-variable tokens. |
| Observability pipeline health | silently empty (wrong columns) | HAVE | Fixed `pipeline_update_timeline` column names. |

### v2.5.4 — Scalability closure (#18)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #18 Scalability | PARTIAL | HAVE | Two blockers closed: (1) **Distributed cache** — `DeltaCacheService` in `cache_service.py` backs an all-replica shared Delta table; replicas read from it before hitting DBSQL, wired via `perf_patches.py` PATCH 5. No Redis/external infra required. (2) **Graph pagination** — `GET /api/scalability/graph` implements BFS cursor-pagination (page_size up to 1000, opaque continuation cursors); removes the 400-node hard cap. Scalability router registered in `main.py`. |

### v2.5.3 — OL producer bridge (#05 Multi-Platform)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #05 Multi-Platform | PARTIAL | HAVE | Added OL producer bridge: external OL-emitting platforms (Snowflake Horizon, BigQuery via OL proxy, Spark + openlineage-spark, Flink, dbt Cloud, Airflow 2.7+) register once and receive a push URL. Lineage flows into `external_ol_bridge_events` via `POST /api/external/ol-bridge/ingest/{source_id}`. No in-platform compute required — platforms own their OL emission; we own ingestion. |

### v2.5.2 — Bulk closure (7 items)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #01 Automatic Discovery | PARTIAL | HAVE | dbt/Airflow import + foreign catalog crawl + manual registration already covered all cases |
| #02 End-to-End Lineage | PARTIAL | HAVE | Added BI consumer detection (`/api/lineage/bi-consumers`) + streaming topology (`/api/lineage/streaming-topology`) |
| #07 Versioned Lineage | PARTIAL | HAVE | Added auto-capture scheduling (`/api/snapshots/auto-capture`) + growth timeline (`/api/snapshots/timeline`). Diff already existed. |
| #11 Data Quality | PARTIAL | HAVE | Added trend history (`/api/dq-rules/trends`) + metrics recording + pipeline expectation sync. Live metrics + propagation already existed. |
| #14 AI/ML Lineage | PARTIAL | HAVE | Feature tables + vector indexes + vector lineage + prompt lineage + model registry all already implemented in `routes/ml.py` |
| #20 Notifications | PARTIAL | HAVE | Added webhook registration + delivery queue. Detection scan (schema/DQ/sensitive) + alert rules already existed. |
| #10 Business Lineage | GAP | HAVE | Already had full glossary (term CRUD, domains, KPIs, term→table linking). Added downstream propagation suggestions (`/api/glossary/propagate-suggestions`) + lineage graph overlay (`/api/glossary/lineage-overlay`). |
| #17 Open Standards | PARTIAL | HAVE | Added live producer: configure endpoint → detect writes via `system.access.table_lineage` → queue OL RunEvents → async delivery. Bidirectional: export + import + produce. |

### v2.5.1 — Initial closures (3 items)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #05 Multi-Platform | GAP | PARTIAL | Federated overlay for foreign catalogs |
| #09 Root Cause | PARTIAL | HAVE | DQ rule violation wired into `trace_root_cause()` |
| #17 Open Standards | GAP | PARTIAL | OpenLineage export + import endpoints |

### Remaining

None — all 20 scorecard capabilities are at HAVE status.

---

## Part E — Design Document Coverage

| Document | Covers | Status |
|----------|--------|--------|
| `lineage-capability-matrix.pdf` | 20-item scorecard | ✅ 20/20 HAVE, 0 PARTIAL, 0 GAP |
| `FEDERATED_LINEAGE_DESIGN.docx` | Tier 1–3 federation | Tier 1, Tier 2, Tier 3 (glossary implemented) |
| `Column Transformation Lineage — Design Document.pdf` | LLM expression inference | `POST /api/analyze-producer` |
| `lineage-plan-capture` project | Runtime capture + parser | `backend/plan_capture/` (mature parser) |

---

## Part F — Table Lineage workspace + UI shell (v2.5.5, this session)

A dedicated front-end that unifies the per-table capabilities into one workspace,
modeled on the Data Lineage POC ("Lineage Explorer"). Backend was already present
(caps 08/09/12/13/14/39); this session wired the UI and made several services
service-principal-friendly.

### Frontend — `frontend/src/components/table-lineage/`

| File | Role |
|------|------|
| `TableLineageWorkspace.tsx` | 3-pane shell: left catalog tree, center lineage graph (reuses `LineageCanvas`), top summary bar (columns/upstream/downstream/updated + capability buttons). Capability buttons open **draggable** panels. Double-click a graph table node re-focuses the workspace. |
| `CatalogTreePanel.tsx` | Catalog → schema → table tree built from the client-side `allTables` index (no new API) with a filter box. |
| `DraggablePanel.tsx` | Reusable floating, draggable panel (grab title bar; multiple can be open; click brings to front). Theme-aware surface. |
| `ImpactPanel.tsx` | Impact + **Consumers by type** + clickable consumer deep-links (cap 08). |
| `RootCausePanel.tsx` | Health-based auto-run trace: status pills, prime suspect, failure path, flagged producers (cap 09, `/api/root-cause/trace`). |
| `GovernancePanel.tsx` | Identity header + tags + classified columns + **Configure classification** (add/remove column & UC-tag rules) (cap 12). |
| `AccessPanel.tsx` | Grantees/accessors/reads/writes stats, identities, grants, recent events (cap 13). |
| `MLModelsPanel.tsx` | Models trained on the table + serving-endpoint status (cap 14). |
| `ColumnTransformationPanel.tsx` | Unified precedence source-of-truth banner (captured plan → CDC → stored LLM → fresh LLM), per-column cards, model dropdown, unified version history across sources, and cross-source version compare (cap 39). Replaces the old `LLMTransformPanel.tsx`. |
| `panelShared.tsx` | Shared panel primitives (loading/error/empty states, stat tiles, sensitivity badges, `parseFqn`). |

Routing: `hooks/useRouter.ts` adds `tableLineage` view + `goTableLineage(table?)`;
opened from the 4th Landing tile. `api/client.ts` adds the impact-consumers,
governance-rule, access, ml-models, root-cause-trace, and analyze-producer
(models/versions/version/compare) methods + types.

### UI shell (app-wide)

| Area | Files | Change |
|------|-------|--------|
| Theme (light/dark) | `tailwind.config.ts`, `styles/globals.css`, `store/themeStore.ts`, `components/ui/ThemeToggle.tsx` | Color tokens (surface/slate/white) converted to CSS variables; `.dark`/`.light` classes on `<html>` flip the whole UI. Persisted to localStorage, applied before first paint. Toggle top-right on Landing, workspace, Toolbar. |
| Home redesign | `components/landing/Landing.tsx` | Sidebar-shell layout: nav (Home/Search/Browse/Lineage Explorer/Impact/DQ/Reports/Settings + admin-gated Admin Dashboard), workspace selector, user profile, top-right bell/help/theme, hero, 4 tiles, global search, Recent Activity (from `/api/notifications`). |
| Logo | `frontend/public/bricktrace-logo.logo`, `backend/main.py` (`GET /bricktrace-logo.png`) | New logo across Landing/Toolbar/workspace/favicon. Stored as `.logo` (not `.png`, which the bundle sync drops via `.gitignore`) and served with an explicit `image/png` media type. |
| Graph theming | `components/graph/TableNode.tsx`, `LineageCanvas.tsx`, `EntityNode.tsx`, `ui/TableTooltip.tsx` | Hardcoded node/tooltip hex (`#161625`/`#12121E`/`#13131F`/`#1E1E2E`) replaced with theme-aware `bg-surface-*`. |
| Admin access | `hooks/useRouter.ts` (`goAdmin`), `Landing.tsx` | Admin Dashboard reachable from the sidebar (admin-gated) after the header-menu was dropped in the redesign. |

### Backend deltas this session

| File | Change |
|------|--------|
| `server/access.py` | grants via `information_schema.table_privileges`; POC-style audit query; single audit scan; identities + reads/writes; polling `_execute_sql`; 14-day default window |
| `server/governance.py` + `routes/governance.py` | `table_type`/`created_by`/`last_altered` in response; `delete_governance_rule()` + `DELETE /api/governance/config` |
| `server/root_cause.py` + `routes/root_cause.py` | `trace_root_cause_table()` + `GET /api/root-cause/trace` (health-based) |
| `server/ml.py` | live model→table derivation from UC registry + MLflow run inputs |
| `server/llm.py` | SDK OAuth call; default `databricks-claude-sonnet-4-6`; full-column prompt; `max_tokens` 4000 |
| `server/producer_source.py` | fetch target columns; `ExportFormat.SOURCE` fix; version-aware analyze; `model` param; **v2.5.6** unified `resolve_column_transformations()` (precedence), `list_all_versions()` + `compare_transformation_versions()` (cross-source) |
| `server/analysis_store.py` | source snapshot column; configurable `PRODUCER_ANALYSIS_TABLE`; per-(entity,target) version keying; get-latest/get-version/list-versions |
| `plan_capture_service.py` | **v2.5.6** configurable `CAPTURED_PLANS_TABLE`/`CAPTURED_CDC_TABLE`; `coalesce(version,1)` for NULL versions; `get_captured_columns()`, `list_captured_versions()`, `get_captured_columns_version()`, `get_captured_cdc_spec()` |
| `routes/impact.py` | `_consumers()` — reader entities grouped by type + resolved deep links |
| `routes/lineage.py` | analyze-producer `models`/`versions`/`version`/`compare`; **v2.5.6** `POST /api/column-transformations` + `/versions` + `/compare` |
| `server/entities.py` | `DASHBOARD_V3` (Lakeview) resolver + `/dashboardsv3/` deep link; `DBSQL_QUERY`/`DBSQL_DASHBOARD` aliases |
| `server/observability.py` | fix `pipeline_update_timeline` column names (`result_state`/`period_*`) — also unblocks pipeline run health |
| `lineage_service.py` | `_execute_sql` polls past the 50s SQL wait cap (fixes schema-lineage `PENDING` 500s) |

### Known gaps / caveats (this session)

- **ML models for the demo table** resolve to empty when the training run's notebook-scoped MLflow experiment notebook was deleted (SP can't read run inputs). Live derivation works for models whose runs the SP can read; app-owned `model_lineage` table is the fallback.
- **Access grants list** shows only what the app SP can see — it needs `MANAGE` on the catalog/schema to enumerate all grantees (granted on `pritam_demo_workspace_catalog` in the demo workspace).
- **`system.access` / `system.billing`** grants for the SP require account-admin; audit-backed sections stay empty without them.
- **~20 service files** still have their own non-polling `_execute_sql` (only `access.py` + `lineage_service.py` fixed); latent, only bites on >50s queries.
- **Deploy vars**: the app is deployed with `--var lineage_catalog=pritam_demo_workspace_catalog --var lineage_schema=bricktrace_lineage` (the default `lattice_lineage` catalog can't be created in the FEVM workspace). These are runtime `--var`s, not persisted in `databricks.yml`.
- **Logo** still carries a baked background; a transparent PNG at `frontend/public/bricktrace-logo.logo` would render cleaner.

## Part G — Cached panels, run health & multi-producer comparison (v2.6.0)

### G1. Per-table capability cache

| File | Role |
|---|---|
| `backend/server/capability_cache.py` | `CapabilityCache` (Delta table `{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.capability_cache`, keyed by `(table_fqn, tab)`); `get/set/evict/evict_table/evict_all/inventory`. `serve_or_compute(table_fqn, tab, compute, actor, refresh)` read-through helper attaches a `_cache` meta block. Always returns the payload + a computed `stale` flag (TTL `CAPABILITY_CACHE_TTL_SECONDS`, default 24h; 256 KB payload cap). |
| `backend/routes/impact.py` · `root_cause.py` (`/trace`) · `governance.py` · `access.py` | Each GET takes `refresh: bool = Query(False)` and wraps its compute in `serve_or_compute`. Impact body extracted to `_compute_impact`. |
| `backend/main.py` | `GET /api/admin/capability-cache` (inventory) · `POST /api/admin/capability-cache/evict?scope=entry\|table\|all` (admin-gated). |
| `frontend/src/api/client.ts` | `CacheMeta` + `CapabilityCacheEntry` types; `_cache` on the 4 responses; `refresh?` param on `getImpact/getRootCauseTrace/getGovernance/getAccess`; `getCapabilityCacheInventory` + `evictCapabilityCache`. |
| `frontend/src/components/table-lineage/panelShared.tsx` | `CacheHeader` — "cached Xh ago / may be stale" badge + Refresh icon; wired into all 4 panels (Governance forces `loadGovernance(true)` after rule edits). |
| `frontend/src/components/AdminDashboard.tsx` | "Capability Cache" section: per-entry `EVICT`, `EVICT TABLE`, `Evict all`. |

### G2. Per-node run health check

| File | Role |
|---|---|
| `backend/server/observability.py` | `get_recent_runs(entity_type, entity_id, limit=5)` — per-run rows from `system.lakeflow.{job_run,pipeline_update}_timeline` grouped by `run_id`/`update_id` (terminal state via `MAX_BY(result_state, period_end_time) FILTER (...)`), LEFT-joined to `system.billing.usage` on `usage_metadata.job_run_id`/`dlt_update_id` for **per-run cost**. Computes verdict, success rate, avg duration + trend, cost total + spike, run/entity deep links. `OBSERVABILITY_LOOKBACK_DAYS` default 90. |
| `backend/routes/observability.py` | `GET /api/observability/runs?entity_type=&entity_id=&limit=&refresh=` — cached via `serve_or_compute` (key `entity:{TYPE}:{id}`, tab `runs:{TYPE}:{limit}`). |
| `frontend/src/api/client.ts` | `EntityRun` / `EntityRuns` types + `getEntityRuns`. |
| `frontend/src/components/graph/EntityNode.tsx` | Activity icon on JOB/PIPELINE nodes → `HealthPopover` (summary tiles + last-5-runs, discount-aware costs, refresh). `fmtDuration` rounds fractional avg. Node cost badge = 30-day serverless total (distinct from popover last-N-runs total — both tooltipped). |

### G3. Multi-producer transformation comparison

| File | Role |
|---|---|
| `backend/server/producer_source.py` | `compare_producers(catalog, schema, table, producers[], actor, force_rerun)` — resolves **each producer from its own source via `analyze_producer`** (NOT `resolve_column_transformations`; a table-keyed captured plan would return identical results for every producer and hide divergence). Builds a per-column matrix (union of target columns × producers), flags `divergent` on `_cmp_key` (expr + source_columns) or present/absent mismatch. |
| `backend/routes/lineage.py` | `POST /api/column-transformations/compare-producers` (needs ≥2 producers). |
| `frontend/src/api/client.ts` | `ProducerCompare` / `ProducerCompareCell` types + `api.compareProducers`. |
| `frontend/src/components/table-lineage/ColumnTransformationPanel.tsx` | Derives producers from graph `edges` whose `target` == focus table; shows "Compare side-by-side" banner + `ProducerCompareMatrix` (rows = columns, cols = producers, divergent rows highlighted) only when 2+ producers. |

### G4. Producer-source fetch + access-denied UX (v2.6.0 fixes)

| File | Role |
|---|---|
| `backend/server/producer_source.py` | `_fetch_pipeline_source` reads the **raw pipeline spec via REST** and handles `notebook.path` / `file.path` / `glob.include` (walks the glob dir, exports each `.py`/`.sql`) — fixes "LLM unavailable" on modern bundle/DLT pipelines. `_fetch_workspace_file` (download API). `_FetchDiag` + `_is_access_error` classify failures; `analyze_producer` returns `reason_code` (`access_denied`/`entity_missing`/`no_source`) + `denied_paths` + `app_service_principal` (from `DATABRICKS_CLIENT_ID`). |
| `frontend/src/components/table-lineage/ColumnTransformationPanel.tsx` | `AccessDeniedNotice` — names the SP + denied paths + CAN_VIEW/CAN_READ grant guidance when `reason_code=access_denied`. |

### Multi-producer demo

`pritam_demo_workspace_catalog.multi_producer_demo.orders_curated` — written by 2 jobs (`mp_producer_a`=239232751351800, `mp_producer_b`=535053242967358); 3 of 4 columns diverge (amount_usd, status, region). SP needs `CAN_VIEW` on both jobs + `CAN_READ` on both producer notebooks for the comparison to resolve.

---

## Part H — Business view, precise dataset lineage & AI graph explanation (v2.6.x, this session)

A plain-language lens over the Table Lineage graph for non-engineers, a fix for the
"everything-connected-to-everything" mesh when collapsing processing nodes, and an
AI narrative of the on-screen graph. All additive; the technical view is unchanged.

### H1. Business view (plain-language lens)

| File | Role |
|---|---|
| `frontend/src/store/lineageStore.ts` | `businessView: boolean` + `setBusinessView` / `toggleBusinessView`; persisted to `localStorage["bricktrace-business-view"]`, read at store init. Entering business view also clears column-level state (`columnEdges`, `selectedColumn`, `expandedNodes`). |
| `frontend/src/lib/businessView.ts` | Pure, deterministic mapping module. `businessEntityLabel` (JOB→Process, PIPELINE→Data pipeline, NOTEBOOK→Code step, QUERY→Query, DASHBOARD→Report; default Process). `businessTableLabel` (MANAGED/EXTERNAL/MATERIALIZED_VIEW→Dataset, VIEW→View, STREAMING_TABLE→Live dataset, VOLUME/PATH→File, EXTERNAL_LINEAGE→External source; default Dataset). `humanizeName` (snake/kebab/camel/dotted → Title Case, preserves already-uppercase acronyms). `businessNodeLabel`, `businessNodeType`, `businessDescription` (table comment, else "built from N sources, feeding M downstream consumers"; entities "A <kind> that moves and transforms data"). `isHiddenInBusinessView` (hides `QUERY`). |
| `frontend/src/components/graph/LineageCanvas.tsx` | Floating "Technical ⇄ Business" toggle (top-left of the canvas). In business view: relabels via the lib, and simplifies the graph (see H2/H3). |
| `frontend/src/components/graph/TableNode.tsx` · `EntityNode.tsx` | `businessView`-gated rendering: friendly label (`humanizeName`) + friendly type badge (no `uppercase`), plain-English description subtitle (TableNode), and hides FQN affordances, column-expand/detail, entity IDs, per-run cost badge, and health popover. |
| `frontend/src/lib/businessView.test.ts` · `store/lineageStore.test.ts` | Mapping/humanize/description/hidden-type coverage + store toggle + persistence. |

### H2. Data-only vs Data + processing

| File | Role |
|---|---|
| `frontend/src/store/lineageStore.ts` | `businessDetail: "data" \| "data_and_processing"` + `setBusinessDetail`; persisted `localStorage["bricktrace-business-detail"]` (default `data_and_processing`). |
| `frontend/src/components/graph/LineageCanvas.tsx` | Sub-toggle shown only in business view. `"data"` hides **all** processing (entity) nodes; `"data_and_processing"` keeps jobs/pipelines and drops only the noisiest (`isHiddenInBusinessView` → QUERY). Hidden nodes are removed and their flow is bridged so the picture stays connected. |

### H3. Precise table→table lineage (`table_edges`) — mesh fix

**Root cause:** each `system.access.table_lineage` row is one real `(source_table → target_table)` pair *with* its mediating entity, but `_build_graph_from_rows` collapsed those rows into sets (`table→entity`, `entity→table`), discarding which source fed which target. The UI's entity-collapse then cross-producted each entity's inputs × outputs, fabricating edges and producing an O(N²) mesh for hub tables (e.g. `pritam_demo_workspace_catalog.silver_dynamic.customers_validated`).

| File | Role |
|---|---|
| `backend/lineage_service.py` | `_build_graph_from_rows` now also collects the DISTINCT precise `(source, target)` pairs per row (`sref != tref`) and returns them as `table_edges`. Trace cache key bumped `trace:` → **`trace:v2:`** so pre-v2 cached traces (no `table_edges`) are ignored. |
| `backend/models.py` | New `table_edges: list[LineageEdge] = []` on `LineageResponse`. |
| `backend/main.py` | `GET /api/lineage/trace` (and scope lineage) return `table_edges`; both apply the truncation filter to `table_edges` alongside `edges`. |
| `backend/perf_patches.py` | Distributed-cache trace patch key bumped to `trace:v2:` (namespace `trace`) in lock-step with `lineage_service`. |
| `frontend/src/api/client.ts` | `table_edges?: LineageEdge[]` on `LineageResponse`. |
| `frontend/src/store/lineageStore.ts` | `tableEdges` state, populated from `data.table_edges` in `setLineageData`; cleared on `reset`. |
| `frontend/src/components/table-lineage/TableLineageWorkspace.tsx` | Passes `tableEdges: data.table_edges` into `setLineageData`. |
| `frontend/src/components/graph/LineageCanvas.tsx` | Data-only view renders `tableEdges` filtered to visible datasets instead of cross-producting; falls back to bridging only if `table_edges` is absent (older cached trace). |
| `tests/test_lineage_service.py` | `test_build_graph_table_edges_are_precise_not_cross_product` (asserts real `a→x`,`b→y`; fabricated `a→y`,`b→x` absent) + self/missing-skip test. |

### H4. AI "Explain this lineage" (lightbulb)

| File | Role |
|---|---|
| `backend/server/llm.py` | `explain_lineage_graph(nodes, edges, focus_table, detail, model)` — builds an id-free prompt (node labels + `A -> B` connections) under `_GRAPH_EXPLAIN_SYSTEM_PROMPT`; returns `{summary, steps:[{title, detail}]}`; never raises (`error` key on failure). |
| `backend/routes/lineage.py` | `POST /api/lineage/explain` (analyze_router). Body `{focus_table, nodes:[{id,label,type}], edges:[{source,target}], detail, model}`. `503` if LLM unconfigured, `400` if no `focus_table`, `500` on error; runs the call in a thread. |
| `frontend/src/components/graph/LineageExplainModal.tsx` | Portaled modal — summary + ordered "source → process → output" walkthrough; caches per `(focus · detail · graph signature)` for the session; regenerate button; Escape to close. |
| `frontend/src/components/graph/LineageCanvas.tsx` | Business-view lightbulb button; posts the on-screen (business-view) `explainNodes`/`explainEdges` so the narrative matches exactly what's shown (respects data-only vs data+processing). |
| `frontend/src/api/client.ts` | `api.explainLineageGraph` + `LineageExplainResult` / `LineageExplainStep` types. |
| `tests/test_server_llm.py` · `tests/test_routes_column_transformations.py` · `frontend/src/api/client.test.ts` | LLM function, route (503/400/ok/bad-detail/500), and client method coverage. |

### H5. Coverage gates after this work

Backend **90.67 %** (1,482 passing); frontend **599** tests (96.4 % lines / 85.4 % branches). Both gates green.
