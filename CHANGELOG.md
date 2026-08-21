# Changelog

All notable changes to BrickTrace are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- **Business view for Table Lineage** — a canvas toggle (**Technical ⇄ Business**, top-left of the graph) that flips the detailed engineering lineage into a plain-language lens for non-engineers, instantly and with no backend call. It relabels technical types into business terms (JOB → "Process", PIPELINE → "Data pipeline", NOTEBOOK → "Code step", VIEW → "View", MATERIALIZED_VIEW/MANAGED/EXTERNAL → "Dataset", STREAMING_TABLE → "Live dataset", VOLUME/PATH → "File"), humanizes `snake_case` names (`orders_curated` → "Orders Curated"), shows a one-line plain-English description per dataset (its curator comment, or a "built from N sources, feeding M consumers" summary), and hides engineering detail (fully-qualified names, column-level edges, job/pipeline IDs, per-run cost, health popovers). The preference persists across sessions. (`frontend/src/lib/businessView.ts`, `businessView` in `lineageStore`.)
  - **Data-only vs Data + processing** — a sub-toggle in business view chooses whether to show just the datasets and how they connect, or the datasets plus the processing steps (jobs/pipelines) that move data between them. "Data only" hides all processing nodes and bridges the flow so the picture stays connected; either way the noisiest ad-hoc query nodes are dropped.
  - **AI "Explain this lineage" lightbulb** — a bulb button (business view) opens a modal with an AI-generated, plain-English explanation of the *current* on-screen graph: an overall summary plus an ordered "source → process → output" walkthrough. Stateless — the frontend posts the visible (business-view) nodes/edges so the narrative matches exactly what's shown. New `POST /api/lineage/explain` (`explain_lineage_graph()` in `llm.py`); results are cached per view for the session and can be regenerated.

- **Column Transformation panel restructured into tabs** — one long scroll became a compact source header (label · version · column count, with an ⓘ precedence-legend toggle) plus **Columns / Analyze / History / Producers** tabs. Columns holds the transformation cards + a filter box (shown when >6 columns), CDC detail, and the access-denied notice; Analyze holds the producer picker + model + (Re-)analyze; History holds the version list, view-a-version, and cross-source diff; Producers holds the multi-producer divergence matrix (auto-runs when 2+ producers write the table). The panel opens wider (460px).
- **AI overview of column transformations** — a new portaled master–detail modal (`ColumnOverviewModal`) gives a plain-English LLM summary of what the table's transformations do plus a per-column explanation, alongside a lineage-graph-style source→transform→target graphic. Backed by `POST /api/column-transformations/overview` (`explain_transformations()` in `llm.py`, cached per table via the shared capability cache).
- **Deep framework analysis for metadata-driven pipelines** — when normal analysis finds no column logic (a generic, config-driven ETL engine), an agentic streaming (NDJSON) second pass detects the config mechanism from the source, reads the producer's parameters, queries the identified config table(s), and derives per-column transformations from that config — narrating each step. New `backend/server/framework_analysis.py` + `POST /api/column-transformations/deep-analyze`.
- **Existing lineage surfaced on open** — when the Column Transformation panel opens without a producer picked, `analysis_store.get_latest_for_table()` finds the newest stored analysis across ANY producer and surfaces it (labelled with its producer), instead of showing a contradictory "No lineage yet".
- **Maroon collapsible rails + colour-coded panels** — the catalog tree and home sidebar are now a deep-maroon collapsible rail (collapses to a slim icon rail); each capability panel gets its own accent identity (coloured top edge + tinted header + icon chip), home tiles match, and panels can be minimized to a dock of chips pinned to the screen bottom. Light-mode legibility tightened (darkened slate ramp + scoped accent-text remaps).

### Tests & tooling

- **Backend test coverage raised to a 90%+ gate.** Coverage went from ~38% (with 210 failing tests) to **90.5%** across `backend/` with **0 failing** (1,423 tests). Added `.coveragerc` (`fail_under = 90`, branch mode, scoped to `backend/`, omitting the standalone offline pipeline `transformation_lineage/` + `backend/plan_capture/` capture-cell wheel + `startup.py`/`perf_patches.py` bootstrap). Run: `pytest tests/ -m "not integration" --cov=backend --cov-fail-under=90`.
- **New unit tests** for every previously-thin module: all `server/*` engines (access, governance, discovery, entities, scd_lineage, column_profiling, schema_change, root_cause, ml, analysis_store, llm) at 97–100%; `lineage_service` (92% isolated), `producer_source` (97%), `transform_service` (92%), `capability_cache`, `observability`, `edge_case_guards`, `cache_service`, `feature_flags`, `federated_sync`, `circuit_breaker`, `validators`, `parallel`; and deep route tests for impact, glossary, dq, ml, diagnostics, snapshots, openlineage, external_sources, pipeline_installer, notifications, scalability, root_cause, plus main.py routes/internals.
- **Fixed 42 pre-existing broken tests** (stale `_execute_sql`/`_sql` mock targets, out-of-date response-shape and endpoint-param assertions, admin-gate expectations) and a **suite-isolation defect**: the process-wide FastAPI app's rate-limiter buckets + auth cache + lineage LRU/cost globals leaked across tests, causing order-dependent 429s (the main cause of the original mass failure). A `conftest.py` autouse fixture now resets them before/after each test.
- **Frontend test suite to a 90%+ gate** (Vitest + React Testing Library + jsdom + v8 coverage): **96% lines / 95% statements / 94% functions / 85% branches**, 572 tests. Covers the api client (100%), stores/hooks/lib (100%), and every panel/component (browse, control-panel, landing, layout, transform-non-canvas, ui, table-lineage — including the new `ColumnOverviewModal`). `frontend/vitest.config.ts` sets the thresholds and excludes the graph/canvas rendering layer (React Flow + ELK), the `App.tsx` shell, and dead code; `src/test/setup.ts` shims jsdom gaps (localStorage/matchMedia/ResizeObserver/pointer-capture). Run `cd frontend && npm run coverage`.
- **Restored the backend gate after the tab/overview/deep-analysis work.** Those features shipped `framework_analysis.py`, new `llm.py` paths, and new lineage routes without tests, which dropped backend coverage to 87.3% and broke 5 tests whose assertions predated the new behaviour. Added `tests/test_server_framework_analysis.py` (framework_analysis 0→100%), extended the `llm.py` tests (`explain_transformations`/`detect_framework_config`/`derive_columns_from_config`/temperature-retry/content-block flattening → 57→97%) and the column-transformation route tests (new `/overview` + `/deep-analyze` → 67→79%), and updated the 5 stale tests. Backend back to **90.5%, 1471 passing / 0 failing**.

### Fixed

- **Deep framework analysis now explains an empty config table** instead of a generic failure. When a metadata-driven framework's config table exists but currently has no rows (common for frameworks that write their config per run), `deep_analyze_stream` short-circuits with a specific `config_empty` reason — *"The config table(s) … are currently empty. This framework writes its column config per run, so run the producing pipeline for this target, then re-analyze."* — rather than calling the LLM and reporting "No columns could be derived." (`backend/server/framework_analysis.py`.)
- **Deep analysis honours the framework's own config key column.** `_query_config_table` accepted the LLM-detected `target_key_columns` but never used them — matching was hardcoded to a fixed `_TARGET_KEYS` list, so a config table keyed by e.g. `tgt_tbl`/`dest_table` matched nothing, only a blind 10-row sample reached the derive LLM, and analysis reported "No columns could be derived" for config that was right there. The detected keys are now unioned with the defaults (`_target_keys`) and threaded through `_focus_on_target`.
- **The `config_empty` short-circuit no longer fires on unproven assumptions.** It previously treated any empty table as proof that nothing could be derived, which produced a permanent dead end in three cases, all now fixed: (1) the detection prompt returns `certain: false` for a table name it only *guessed* from a variable — an empty guess proves nothing, so analysis falls through and the guess is called out; (2) a table that couldn't be `SELECT`ed was silently reported as "empty", sending the user to re-run their pipeline when the real fix was a `GRANT` — unreadable and empty tables are now tracked separately and each named with its own remediation (`_blockers_note`); (3) deriving from source + parameters alone — the documented fallback for the no-tables case — became unreachable whenever an empty table was present, so a framework passing its column map as a pipeline parameter stopped working; the guard now falls through when parameters are available.
- **`reason_code` is now actually surfaced, and on every failure path.** The field was added to the deep-analyze result event but nothing read it (the panel showed only `detail`), and only 1 of 4 non-derived results set it. All four now carry a code (`no_source`/`detect_failed`/`config_empty`/`no_columns`), and the panel renders a reason-specific headline plus a next step (`DEEP_REASONS`/`DeepOutcome`) instead of appending one more line to the capped log — which also removes the triple-reported "config table is empty" message. The vocabulary is defined once (`TransformReasonCode` in `backend/models.py`, mirrored in `client.ts`) and shared by both carriers, so the panel's reason → label mapping can't silently fall through and `DeepAnalyzeResultEvent.reason_code` is no longer an open `string` while its sibling is a closed union.
- **`config_tables` on the result event no longer changes meaning between branches** — it was the full detected list on success but only the empty subset on failure. It is always the full list now; `empty_config_tables` and `unreadable_config_tables` carry the subsets. Also: duplicate table names from the free-form detection JSON are de-duplicated (they were queried and named twice), `total_rows` is read defensively so a partial dict can't `KeyError` mid-stream, and entity types reach the UI as friendly nouns (`MATERIALIZED_VIEW` → "materialized view", missing → "producer") instead of raw upper-snake identifiers.
- **Business "Data only" mesh fix now applies to the main canvas too.** `App.tsx`'s trace loader didn't forward `data.table_edges` into `setLineageData`, and the store defaults the field to `[]` — so every load through the app shell reset it and `LineageCanvas` took its cross-product fallback, reproducing the exact dense mesh the fix below was meant to remove. Only the Table Lineage workspace passed the field through. (Note `/api/lineage` — the schema-scoped endpoint — genuinely does not populate `table_edges`, since only `get_lineage_trace` builds the graph via `_build_graph_from_rows`; that path still uses the bridging fallback by design.)
- **Business "Data only" view showed a dense "everything → everything" mesh** for hub tables (e.g. `…silver_dynamic.customers_validated`). Collapsing the processing steps reconstructed dataset edges by cross-producting each job/pipeline's inputs × outputs, fabricating pairs that were never real dependencies. The backend already reads a precise `(source_table → target_table)` pair for every `system.access.table_lineage` row but only kept the entity-routed edges; it now also returns those exact pairs as `table_edges` on `/api/lineage/trace`, and the Data-only view renders them directly instead of cross-producting. (`_build_graph_from_rows` in `lineage_service.py`, `table_edges` on `LineageResponse`.)
- **Cross-source diff false positives** — `compare_transformation_versions` keyed on expression + source_columns, but the Spark-plan parser emits cosmetically different source-column sets for two captures of the SAME plan, so every column showed as "changed" against an identical expression. It now keys on the normalized expression (what the diff actually renders).
- **Analyze producer scoping** — the Analyze tab's producer picker listed every entity in the lineage graph (the whole pipeline); it is now scoped to producers of the selected table (an edge into the focus table), matching the Producers tab.
- **`llm.py` robustness across model endpoints** — retry-without-`temperature` for models that reject the parameter, flatten Anthropic content-block lists to text, decode base64/JSON config cells, and a meaningfulness gate that is all-or-nothing (reject only when EVERY column comes back UNKNOWN) so struct columns (`_lineage`/`_dq`) aren't dropped for some models. Specific `reason_code`s replace the generic "LLM unavailable".
- **`build_service.py` runtime crash after the notebook-path refactor** — `submit_build_job` and `is_build_configured` still referenced the removed module-level `PIPELINE_NOTEBOOK_PATH` constant (`NameError` on build submit). Both now use `get_pipeline_notebook_path()` / the resolved local path.

---

## [2.6.0] - 2026-07-29

> **Operational lineage — cached capability panels, node-level run health, and multi-producer transformation comparison.** The Table Lineage workspace gets a persistent per-table cache with refresh + admin eviction, every job/pipeline node gains a run-health check with per-run cost, and tables written by more than one producer can be compared column-by-column to catch divergent logic. Plus fixes that make the LLM Column Transformation path work on modern (glob/file) pipelines and clearly explain permission gaps.

### Added

- **Per-table capability cache** — Impact, Root Cause, Governance, and Access panel results are persisted per `(table, tab)` in an app-owned Delta table (`capability_cache`) and served instantly on reopen (measured ~25× faster; cold Access ~78s → warm ~3s). Each panel shows a **"cached Xh ago / may be stale"** badge (24h TTL, `CAPABILITY_CACHE_TTL_SECONDS`) and a **Refresh** icon that forces a live recompute. (`backend/server/capability_cache.py`, `serve_or_compute()`.)
- **Admin capability-cache controls** — `GET /api/admin/capability-cache` (inventory) and `POST /api/admin/capability-cache/evict?scope=entry|table|all`. The Admin dashboard gains a "Capability Cache" section with per-entry, per-table, and evict-all actions.
- **Per-node run health check** — every **JOB** and **PIPELINE** graph node has an activity icon opening a health popover: verdict (Healthy / Degraded / Failing) + success rate, average duration with a slower/faster **trend arrow**, total cost over the window with a per-run **spike flag**, and the **last 5 runs** — each with status, duration, **real per-run cost** (joined from `system.billing.usage` on `job_run_id` / `dlt_update_id`), and a deep link to the run. `GET /api/observability/runs?entity_type=&entity_id=&limit=&refresh=` (cached via the capability cache). (`observability.get_recent_runs()`, `EntityNode.tsx` `HealthPopover`.)
- **Multi-producer column-transformation comparison** — when a table is written by 2+ producers, the Column Transformation panel shows a **"Compare side-by-side"** matrix (rows = target columns, columns = producers) that flags where producers compute the same column differently. `POST /api/column-transformations/compare-producers`. Each producer is resolved from **its own** source (per-entity LLM), not the table-level captured plan — so genuine divergence is surfaced rather than masked. (`producer_source.compare_producers()`, `ProducerCompareMatrix`.)
- **Actionable "access denied" on the Column Transformation panel** — when the app can't read a producer's source, the panel now names the exact resource(s) and the app service-principal to grant, instead of a generic "LLM unavailable". Backend returns a structured `reason_code` (`access_denied` / `entity_missing` / `no_source`) with `denied_paths` + `app_service_principal`.

### Changed

- **`OBSERVABILITY_LOOKBACK_DAYS`** default 30 → 90 so recent-runs and health surface data in demo/low-activity workspaces.
- **README Quick start** — documents Node/npm + Databricks CLI prerequisites, the `npm ci` frontend build step, the full set of required deploy `--var`s (not just `warehouse_id`), and that `bundle run` needs the same vars as `bundle deploy`.

### Fixed

- **"Build pipeline not configured" when env var resolved late** — `build_service.py` no longer uses a module-level `PIPELINE_NOTEBOOK_PATH` constant (which was empty if the env hadn’t propagated at import time). Replaced with `get_pipeline_notebook_path()` — a lazy-cached resolver with a 3-step discovery order: (a) `PIPELINE_NOTEBOOK_PATH` env (whitespace-stripped; empty/whitespace = unset), (b) if `DATABRICKS_APP_NAME` is set, calls `apps.get` to discover the deployed `source_code_path` and appends `/notebooks/run_pipeline`, normalizing `/Users` or `/Shared` prefixes to `/Workspace/...`, (c) else returns `""` (fail-closed). `GET /api/transform/build-configured` now also returns `notebook_path` for frontend diagnostics. Tests use `_reset_pipeline_notebook_path_cache()` instead of module reload.
- **LLM Column Transformation was mislabeled "unavailable" on modern pipelines** — `_fetch_pipeline_source` only handled `notebook`-style pipeline libraries. Bundle/DLT pipelines that declare source via `glob.include` (a directory of `.py`/`.sql`) or `file.path` yielded no source → "No source code available". Now reads the **raw pipeline spec via REST** (older SDK versions in our pinned range silently drop the `glob` field on typed deserialization), walks the glob directory, and exports each file. Added `_fetch_workspace_file` (download API for plain files).
- **Multi-producer comparison masked divergence** — first implementation resolved each producer via table-level precedence, so a table-keyed captured Spark plan returned identical results for every producer (0 divergent). Fixed to resolve each producer from its own source.
- **Run-health duration formatting** — `avg_duration_seconds` is a float; unrounded `secs % 60` rendered as `29.6000000000000023s`. Now rounded.
- **`grant_app_access.sh` aborted under `set -u`** — a bare `$LINEAGE_SCHEMA` abutting a multibyte ellipsis was parsed as part of the variable name, aborting before the app-owned schema was created. Braced the vars; also committed the script's executable bit so fresh clones can run it directly.

---

## [2.5.6] - 2026-07-27

> **Column Transformation Lineage — unified precedence + cross-source versioning.** The LLM Transform panel is reworked into **Column Transformation Lineage**, which resolves a table's per-column derivation best-source-first (mirroring the reference tool): captured Spark plan → captured CDC spec → stored LLM version → fresh LLM. Version history now spans both sources, and any two versions can be diffed — including a captured plan against an LLM deduction.

### Added

- **`POST /api/column-transformations`** — unified resolver. Returns columns + a `source` (`plan_capture` / `cdc_spec` / `stored` / `llm`) and a source label, picking the best available source. `force_rerun` skips captures/cache to a fresh LLM pass.
- **`POST /api/column-transformations/versions`** — merged version list across sources (captured plans + LLM analyses), each with a `ref` (`plan_capture:2`, `llm:6`) and source tag.
- **`POST /api/column-transformations/compare`** — diff any two version refs, including **cross-source** (captured plan vs LLM); per-column added/removed/changed with a `cross_source` flag.
- **`plan_capture_service`**: `get_captured_columns()`, `list_captured_versions()`, `get_captured_columns_version()`, `get_captured_cdc_spec()` — table-level captured-plan reads used by the resolver.
- **Configurable captured-plan tables** — `CAPTURED_PLANS_TABLE` / `CAPTURED_CDC_TABLE` env vars (bundle vars `captured_plans_table` / `captured_cdc_table`) let the reader point at wherever the offline `lineage_capture` wheel writes (e.g. the `lineage_explorer` schema), not just the app-owned schema.
- **`PIPELINE_NOTEBOOK_PATH`** restored in `databricks.yml` as the portable `${workspace.file_path}/notebooks/run_pipeline` — fixes "Build pipeline not configured" without the empty-string value that previously broke the Apps config update.
- **Makefile** — encodes the workspace `--var` overrides so a compute/config change or plain redeploy never drops env (`make redeploy` / `run` / `deploy` / `diagnostics` / `logs`).

### Changed

- **Column Transformation panel** — renamed from "LLM Transform"; adds a precedence-chain legend, a color-coded source-of-truth banner, richer per-column cards (category badge + `src → target` flow + expression), unified version history, and cross-source compare. (`ColumnTransformationPanel.tsx` replaces `LLMTransformPanel.tsx`.)
- **`plan_capture_service`** — NULL `version` handled via `coalesce(version, 1)` when ordering/matching captured plans.
- **`APP_VERSION` / package version → `2.5.6`**.

### Fixed

- Captured-plan lineage now actually resolves: the reader was pointed at the empty app-owned schema instead of the capture project's `lineage_explorer.captured_plans`, and the SP lacked `USE SCHEMA` on that schema.

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
