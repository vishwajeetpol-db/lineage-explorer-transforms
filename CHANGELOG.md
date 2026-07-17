# Changelog

All notable changes to BrickRoute are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.4.0]

> Adds a new **Control Panel** — admin-gated toggles for three opt-in, higher-cost/higher-risk capabilities, all OFF by default: **Runtime Plan Capture** (captures Spark's exact Analyzed Logical Plan from opted-in pipelines, for transformation logic the static parser can't read from source), **Captured-Plan Precedence** (additively surfaces that captured expression in the existing transformation drill-down), and **Federated Sync** (an admin-curated registry of known peer workspaces layered on the existing Delta Sharing overlay — a v1 scaffold, not live cross-workspace sync). See [docs/architecture.md](docs/architecture.md), [docs/capabilites.md](docs/capabilites.md), and [docs/testing_plan_for_Combined_App.md](docs/testing_plan_for_Combined_App.md) for full detail, including an explicit Known Gaps / follow-ups list.

### Added

- **Control Panel** (`backend/feature_flags.py`, `frontend/src/components/control-panel/`) — a Delta-backed feature-flag registry with three capability cards (cost/risk ratings, side effects, access requirements with a live-check button). Reachable from the header menu or `?controlPanel=true`. Reading flags is open to any authenticated user; toggling a flag is admin-gated (same `ADMIN_GROUP_NAME` check as `/api/cache/invalidate`). Every flag's *effective* state is the AND of its persisted DB value and a hard ops-level env-var kill switch (`ENABLE_PLAN_CAPTURE`, `ENABLE_CAPTURED_PLAN_PRECEDENCE`, `ENABLE_FEDERATED_SYNC` — default `true`), so an operator can force a capability off instantly without depending on the SQL warehouse being reachable.
- **`GET /api/control-panel/flags`**, **`POST /api/control-panel/flags/{flag_id}`**, **`GET /api/control-panel/access-check/{flag_id}`** — Control Panel flag read/write/access-check endpoints.
- **Runtime Plan Capture** (`lineage_tracking.plan_capture` flag) — vendored the `lineage-plan-capture` source project's `capture.py`/`plan_parser.py` into `backend/plan_capture/` (adapted so `DEFAULT_TABLE`/`DEFAULT_SPEC_TABLE` derive from this app's `LINEAGE_CATALOG`/`LINEAGE_SCHEMA` instead of the source project's hardcoded catalog). New `backend/plan_capture_service.py` provides the app-side, flag-gated *read* path (`get_plan_capture_status()`, `get_captured_expression()`); the *write* path runs only inside a pipeline notebook that explicitly opts in (two added cells — see docs/capabilites.md), never inside the app process itself.
- **`GET /api/control-panel/plan-capture/status`** — status card endpoint (captured plan/CDC-spec counts, distinct target tables, table reachability).
- **`GET /api/transform/captured-expression`** — returns the runtime-captured expression for a column when Captured-Plan Precedence (`column_transformation.captured_plan_precedence` flag, depends on Runtime Plan Capture) is enabled and a plan has been captured for it; `{"captured": null}` otherwise. Additive only — does not change the existing static-parse transformation trace.
- **Federated Sync** (`federated_sync.cross_workspace` flag) — new `backend/federated_sync.py`: an admin-curated `federated_peers` registry cross-referenced against the existing `lineage_service.get_sharing_overview()` so a shared boundary node can be labeled as a known peer. `GET /api/control-panel/federated/status`, `GET /api/control-panel/federated/peers`, `POST /api/control-panel/federated/peers` (admin).
- **Frontend**: `frontend/src/api/controlPanel.ts` (API client), `frontend/src/store/featureFlagStore.ts` (Zustand store + `useFeatureFlagEnabled` selector), `frontend/src/components/control-panel/` (`ControlPanel.tsx`, `ModuleSection.tsx`, `FeatureToggleCard.tsx` using Radix `Switch`, `ImpactBadges.tsx`, `AccessRequirementsModal.tsx`), a new `controlPanel` route in `useRouter.ts` (`?controlPanel=true`), and a "Control Panel" entry in `HeaderMenu.tsx`.
- **New docs**: [docs/architecture.md](docs/architecture.md) (2.4.0 module architecture + data flow + Known Gaps), [docs/capabilites.md](docs/capabilites.md) (capability-by-capability reference with pipeline opt-in steps), [docs/testing_plan_for_Combined_App.md](docs/testing_plan_for_Combined_App.md) (unit/frontend/manual-QA test plan for the additions above).
- **Pydantic models** (`backend/models.py`): `AccessRequirement`, `FeatureFlagCard`, `FeatureFlagsResponse`, `PlanCaptureStatus`, `FederatedPeer`, `FederatedSyncStatus`.

### Changed

- **`APP_VERSION` bumped to `2.4.0`** (`backend/main.py`) — was still `2.2.0`, out of sync with this changelog since the 2.3.0 release.
- **`frontend/package.json` version bumped to `2.4.0`** — was still `2.2.0`, same drift as above.
- **`docs/ARCHITECTURE.md`** brought current: version header updated from the stale `2.2.0` / `2026-06-29` pin to `2.4.0`, with a new §1.2 summarizing the 2.4.0 additions, updated directory structure, a new Control Panel endpoints table, and updated grants/env-var reference for the five new app-owned tables (`feature_flags`, `feature_flags_audit`, `captured_plans`, `captured_cdc_specs`, `federated_peers`).

### Known limitations (see docs/architecture.md §5 for full detail)

- Federated Sync is an admin-curated registry cross-referenced against existing sharing metadata — it does **not** implement live cross-workspace API calls, trust handshakes, or peer-initiated sync jobs. Treat it as informational, not a trust boundary.
- Runtime Plan Capture has no automated installer; opting a pipeline in is a manual, documented two-cell addition to the pipeline notebook.
- Captured-Plan Precedence is additive (shown alongside the static-parse result) — it does not yet change which upstream columns the transformation BFS itself walks.
- `check_access_requirements()` can verify `CREATE TABLE` and `SELECT` requirements automatically; `WRITE VOLUME`, `CAN_MANAGE`, and "Workspace Admin" requirements currently return an unverifiable (`satisfied: null`) result.

---

## [2.3.0] - 2026-07-14

> Adds **transformation lineage diagnostics** — when a build materialises nothing the app now explains *why* (no producer found, producer outside the discovery window, producer source unreadable) instead of a generic "not generated yet". Also aligns the producer-discovery window to 1 year across all entry points and adds TTL caching to the new diagnostic path.

### Added

- **`GET /api/transform/diagnose`** — new endpoint that explains why a table has no transformation lineage after a build. Inspects `system.access.column_lineage` for the table's producer history relative to the discovery window, plus the most recent extraction report, and returns one of four actionable reason codes: `no_producer` (source/base table with no tracked writer), `producer_outside_window` (writer existed but last ran before the lookback cutoff), `producer_unresolved` (recent writer found but parsing failed), `unknown` (system.access unreadable).
- **`TransformDiagnosis` Pydantic model** (`backend/models.py`) — structured response carrying `reason_code`, `title`, `detail`, `last_produced_at`, `days_ago`, `in_window`, and `skip_reasons`; consumed by the diagnose endpoint and the frontend empty-state panel.
- **`diagnose_missing_lineage()`** (`backend/transform_service.py`) — service function backing the endpoint. Results are cached via the shared `_transform_cached_fetch` TTL cache so repeated UI polls don't re-hit `system.access.column_lineage`.
- **`_latest_extraction_skip_reasons()`** (`backend/transform_service.py`) — best-effort helper that reads the most recent `lineage_extraction_reports` row and surfaces skip-reason keys (e.g. `no_resolvable_tasks`) to refine the diagnostic message.
- **`DISCOVERY_LOOKBACK_HOURS` module constant** (`backend/transform_service.py`, `backend/build_service.py`) — mirrors the build's discovery window (default `8760` h = 1 year, overridable via env var) so the diagnose endpoint and build always share the same window definition.

### Changed

- **Discovery lookback window aligned to 1 year across all entry points** — `notebooks/run_pipeline` notebook default raised from `1080` h (45 d) to `8760` h; `transformation_lineage/config.py` `discovery_lookback_hours` raised from `24` h to `8760` h. Previously a direct/manual pipeline run without a widget override would silently find no producers for tables written on a quarterly or monthly schedule.
- **`DISCOVERY_LOOKBACK_HOURS` forwarded to the build job** — `build_service.py` now passes the env-var value as a `base_parameters` entry so app-triggered builds respect the same window as the diagnose endpoint.

### Fixed

- **Tautological diagnostic title when date parsing fails** — if `system.access.column_lineage` returns a `last_produced` timestamp that `fromisoformat` cannot parse, `days_ago` stays `None` and the previous title read "Producer last ran outside the window — outside the X-day window". Title is now built conditionally: known age → "Producer last ran N days ago — outside the X-day window"; unknown age → "Producer last ran outside the X-day discovery window".

---

## [2.2.0] - 2026-06-29

> Matures the **expression-level transformation lineage** engine — the app's defining capability: reconstruct the actual SQL/PySpark expression behind every column (not just UC's dependency edges), across every producer type. Adds multi-entity-type coverage, the dedicated app-owned store, opt-in builds, admin invalidate controls, and a battery of correctness fixes verified against UC `column_lineage`. Docs (README, ARCHITECTURE, DESIGN, REFERENCE) brought current with the code.

### Added

- **On-demand, opt-in transformation lineage** — clicking a column no longer auto-builds. UC column lineage is the free default; the transformation popup offers an explicit **Generate** action with a compute-cost warning. Adds `needs_build` + empty-state panel states.
- **Multi-entity-type column transformation lineage** — beyond notebook/python-file jobs: **SQL-file tasks** (resolver `sql_task` branch); **materialized views, views, streaming tables, and SQL-defined DLT** via new **definition-based resolution** (parse the object's own `SHOW CREATE TABLE` — no discovery, no system-table lineage lag); **Lakeflow/DLT pipelines** via `PIPELINE` discovery + `pipelines.get` library resolver.
- **Dedicated app-owned lineage store (Option A)** — all transformation edges materialize into one fixed `LINEAGE_CATALOG.LINEAGE_SCHEMA` store owned by the app service principal; node-ids carry the real data catalog, so the app SP needs **zero write** on any data catalog.
- **Empty-state** for columns with no transformation lineage (external/shared source, unsupported producer, or not yet generated) — replaces the misleading blank-but-green popup.
- **Admin "Invalidate transformation lineage" controls** — the admin dashboard now has *Flush cache* (clear in-memory freshness/edges/trace caches, no data loss) and *Wipe lineage* (delete all stored transformation-lineage tables so everything shows "not built" and rebuilds fully re-parse; the expensive audit-path and LLM-expression caches are retained). New admin-gated `POST /api/transform/invalidate?scope=cache|table|all` endpoint + `clear_transform_lineage()`.
- **`FORCE_REPARSE` build flag** — a forced "Regenerate" / clear-and-rebuild now bypasses the content-version early-termination so the parser actually re-runs on byte-identical sources (previously a regenerate of unchanged content silently no-op'd). Threaded config → run_pipeline → build_service (`force_rebuild ⇒ force_reparse`).
- **Query-history fallback resolver** — tables with no tracked producing entity (`entity_type = NULL`: ad-hoc SQL, SQL editor, scripts) are now recovered from `system.query.history` by matching the most recent FINISHED write statement whose parsed output is the target table. Degrades gracefully (`no_producing_query` skip) when the service principal can't see the producing query (query history is identity-scoped — needs broad query-history visibility to cover other users' ad-hoc tables).
- **Persistent build control in the transformation panel header** — the "Generate / Regenerate" button is now always visible: enabled when lineage is missing or stale, and **grayed when already built** (so it's clear it exists) while still allowing a force-rebuild. Previously the button only appeared in the not-built state, so an already-built table showed no build affordance at all.

### Changed

- **Transformation popup UX** — target column rendered on top with upstream cascading down; `fitView` zoom capped (small graphs no longer magnified ~3×); persistent zoom-stable edge labels (category + expression, no hover required); responsive canvas height. **Removed the depth slider** — the popup always shows the selected column's full end-to-end transformation lineage (depth is not a meaningful knob for a single column).
- **`LINEAGE_WINDOW_DAYS` reconciled to 365** across code + docs, with the producer-staleness semantics documented (the window is max producer staleness before lineage drops off; pair with `event_date` partition pruning to keep a wide window cheap).
- **Versions reconciled to 2.2.0** — `APP_VERSION`, `frontend/package.json`, and the docs were drifting (1.3.0 / 1.0.0 / 2.1.0); now aligned.

### Fixed

- **CTE (`WITH ... AS`) resolution in the SQL parser** — outer-query references to a CTE alias (`SELECT cust.total_revenue FROM cust ...`) now resolve *through* the CTE to its base source column (`customer_lifetime_value.lifetime_revenue`), and bare columns inside a CTE attribute to that CTE's own FROM table (not the statement's pooled tables). Previously CTE-based tables (e.g. `gold.executive_summary`) produced only phantom unresolved-external edges. Surfaced by a full scan-and-verify of every built table against UC `column_lineage`; all 15 buildable producers now verify clean.
- **Cross-column-name edge contamination (edge-endpoints join)** — the serve-table builder matched a derived column's source by **column name** against the artifact's read set. Since a whole notebook shares one transformation node, an output column cross-joined to *every* source table exposing a same-named column (e.g. `gold.customer_orders.customer_id` gained spurious edges from `raw_customers`, `raw_orders`, `fct_orders`). Each derive edge now records its exact resolved source node id and the join pins the source by id — eliminating the cross-join. `order_count` went 7→1 edges, `customer_id` 6→1, etc.
- **Self-loop edges** — a mis-resolved alias could attribute a column to its own output table (`x.col ← x.col`), rendering as a target with no upstream. Guarded at parse time (graph builder) and filtered at read time.
- **Duplicate parallel edges** — the BFS now dedups by (source, target) column pair, so a column referenced both bare and alias-qualified renders as one edge, not two.
- **`sqlparse` pinned to `0.4.4`** in the build job (newer 0.5.x tokenizes multi-statement SQL differently).
- **Per-table read scoping (regression)** — transformation reads served edges from the single global-latest `pipeline_run_id`. Because each build is scoped to one table, building table B made table A's column lineage vanish (e.g. building the MV blanked `dim_customers.full_name`). Reads now resolve the latest run that actually built the requested table (`dst_fqn`), so every built table stays viewable simultaneously.
- **Change detection ignored the parser version** — early termination keyed only on source-content SHA, so a deployed parser fix silently no-op'd on byte-identical objects (the streaming-table fixes never re-ran). Added `PARSER_VERSION` folded into the version-check token; bumping it forces a one-time re-parse of all artifacts.
- **Streaming tables now emit transformation edges** — with the change-detection fix above re-parsing the definition, `STREAM(...)` / single-source streaming tables produce correct column edges (`cast`, `upper`, `concat`, …). Verified end-to-end on `st_orders_norm`.
- **Python-defined DLT (`@dlt.table` / `@dlt.view`)** — the AST parser now walks decorated dataset functions' return chains and emits column mappings (previously only `saveAsTable` sinks were handled). Also resolves `spark.readStream.table` / `dlt.read` / `dlt.readStream` sources. Verified end-to-end on `dlt_order_enriched`.
- **SQL parser** — detect `MATERIALIZED VIEW` / `STREAMING [LIVE] TABLE` / `LIVE TABLE` / `VIEW` / `OR REFRESH` output targets; unwrap `STREAM(...)` source reads; resolve unqualified columns against a single known source table.
- **`run_pipeline`** — `sys.dont_write_bytecode = True` to avoid WSFS `__pycache__` `AsyncFlushFailedException` when importing the package from a Workspace path.
- **Transform read/build paths** resolve the dedicated lineage store consistently (was deriving the store from the selected table's own schema).

### Known limitations

- **Delta Sharing / Lakehouse Federation tables** — transformation lineage is not derivable (the producing code runs in another account); detected and surfaced as an external source. A local notebook that **reads** a shared table into a local table **is** captured (the shared table appears as an upstream source).

---

## [2.1.0] - 2026-06-22

### Fixed

- **CRITICAL: Orphaned polling race** — `closePanel()` could not cancel scheduled `setTimeout`, causing stale poll to re-open panel. Added triple guard in `transformStore.ts`.
- **CRITICAL: Double-open race** — rapid column clicks caused parallel `openPanel` chains to overwrite each other. Added staleness guard after each await.
- **BuildSubmitResponse type** — added `'fresh'` status and `message` field to match backend.
- **F401 lint errors** (10+ files) — removed all unused imports across the codebase.

### Changed

- ARCHITECTURE.md updated to v2.1.0 with race condition documentation.

---

## [2.0.0] - 2025-06-22

### Summary

Unified release consolidating the standalone Streamlit-based transformation lineage app (`transformation-lineage_maincode`) into the combined FastAPI+React architecture. **All functionality is now self-contained** with zero external code references.

### Added

- **Transformation Lineage Panel** — ReactFlow-based interactive DAG for column-level transformation drill-down (replaces D3.js/Streamlit iframe approach)
- **Lineage Builder** (`build_service.py`) — serverless job submission and real-time progress polling with 8-step pipeline visualization
- **Transform Service** (`transform_service.py`) — BFS backtracking engine with single-flight coalescing, parallel SQL, and memory-bounded caching
- **Frontend Transform Components** — TransformPanel, TransformCanvas, TransformNode, TransformEdge, BuildProgress, FreshnessBadge, PruningControls
- **Pipeline Library** (`transformation_lineage/`) — full 8-phase orchestrator embedded in the app with ThreadPoolExecutor parallelism
- **Run Pipeline Notebook** (`notebooks/run_pipeline`) — self-contained notebook executed by build jobs
- **Expression Enrichment** — LLM-powered PySpark→SQL translation via `ai_query` (best-effort, non-blocking)
- **Freshness-aware caching** — separate 1h TTL transform cache (vs 8h main lineage cache)
- **Admin Transform Cache** endpoint in `/api/admin/status`
- **ARCHITECTURE.md** — comprehensive technical documentation
- **CHANGELOG.md** — this file

### Changed

- **Tech stack migration**: Streamlit + D3.js → FastAPI + React + ReactFlow + ELK.js
- **LineageBuilder class** refactored into `build_service.py` (pure functions, no Streamlit state)
- **Graph renderer** replaced: D3 force-directed iframe → ReactFlow with ELK hierarchical layout in Web Worker
- **Job submission** now uses REST API with `requests` library (timeout protection, better error handling)
- **Progress mapping** uses structured `BuildJobStatus` Pydantic model instead of dict returns
- **Cache architecture**: moved from Streamlit `@st.cache_data` to thread-safe `TTLCache` with memory bounds
- **Pipeline notebook path**: auto-derived from deployment location (no manual PIPELINE_NOTEBOOK_PATH required)
- **Error handling**: all transform endpoints return sanitized errors (no internal paths)
- **Input validation**: all transform API params validated with strict regex

### Removed

- Streamlit dependency (`streamlit>=1.28.0`)
- D3.js force-directed graph renderer (`graph_renderer.py`)
- Streamlit-specific session state management
- `@st.cache_resource` / `@st.cache_data` patterns
- External code references to `transformation-lineage_maincode/`
- `WorkspaceClient` caching via Streamlit decorators

### Performance

- **Single-query freshness**: COUNT+MAX in one pass (2x vs sequential exists+count)
- **Pre-indexed upstream adjacency**: O(1) neighbor lookup in BFS (vs O(n) scan)
- **Parallel SQL execution**: 4-thread pool for transform queries
- **Lightweight size estimation**: 10-50x faster cache sizing (heuristic vs JSON serialization)
- **Single-flight coalescing**: bounded per-key lock pool (512 max) prevents thundering herd
- **Early termination**: pipeline skips phases when no new/changed artifacts detected
- **Batch version checking**: single SQL query checks all artifact SHAs at once
- **Node deduplication**: reversed-scan dedup in Phase 4 prevents duplicate writes
- **8-thread parse parallelism**: concurrent sqlparse calls (GIL released in C extensions)

### Security

- Input validation on all transform endpoints (`_IDENTIFIER_RE`, `_FULL_NAME_RE`)
- Admin-only cache invalidation (identity-gated, not IP-gated)
- Rate limiting per user identity (token-hashed)
- CSP headers prevent XSS on user-supplied table/column names
- Error sanitization strips internal paths from API responses

---

## [1.3.0] - 2025-06-15

### Added

- Catalog-wide lineage (omit schema for full catalog graph)
- Cross-catalog trace via `system.access.table_lineage` BFS walk
- Delta Sharing overlay (provider + recipient boundaries)
- Excel export with styled multi-sheet workbook
- Admin ops dashboard with P50/P95/P99 latency, memory, cache inventory
- Live mode for admins (bypass cache for real-time system table reads)
- Deep-link support (`?table=catalog.schema.table`)

### Changed

- Per-user rate limiting (token-hashed, replaces IP-based)
- Security headers middleware (CSP, X-Frame-Options)
- 64-thread pool for blocking SDK calls

---

## [1.2.0] - 2025-06-01

### Added

- Column-level lineage from `system.access.column_lineage`
- Lazy column loader (per-table on-demand)
- Schema-wide column lineage for transitive tracing
- Cache snapshot API for admin monitoring

---

## [1.1.0] - 2025-05-15

### Added

- Entity resolution (job/pipeline/notebook display names)
- Serverless cost per entity (30-day `system.billing` aggregation)
- View modes (Tables, Pipelines, Full)
- Memory-bounded LRU cache with TTL

---

## [1.0.0] - 2025-05-01

### Added

- Initial release: table-level lineage visualization
- FastAPI + React + ReactFlow architecture
- Databricks Apps deployment via DABs
- Catalog/schema browsing with search
- Global search across all tables
- Table lineage DAG with ELK.js layout
