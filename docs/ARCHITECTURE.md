# NEXUS Lineage — Architecture & Functionality Reference

> **Version**: 2.2.0  
> **Last Updated**: 2026-06-29  
> **Tech Stack**: FastAPI · React · TypeScript · ReactFlow · ELK.js · Databricks Apps · DABs

---

## 1. Overview

NEXUS Lineage is a unified, self-contained Databricks App that provides:

1. **Table-level lineage** — end-to-end DAG visualization across all Unity Catalog catalogs
2. **Column-level lineage** — traced from `system.access.column_lineage` edges
3. **Expression-level transformation lineage** — reconstructs the *actual SQL/PySpark expression* behind each column (cast, sum, concat, CASE, window, CTE chains), tagged with a transform category, by parsing the producing code across **every** producer type. Served as a per-column upstream drill-down with BFS backtracking. *(This is the app's defining capability — see §1.1.)*
4. **Lineage Builder** — serverless job orchestration for building transformation lineage on-demand (opt-in, compute-cost-gated)
5. **Delta Sharing** — provider/recipient boundary visualization
6. **Serverless cost** — per-entity 30-day billing from `system.billing`
7. **Admin dashboard** — real-time P50/P95/P99 latency, memory, cache inventory, and transformation-lineage invalidate controls
8. **Excel export** — styled multi-sheet workbook of lineage data

All functionality is **self-contained** — zero external code references. The app deploys as a single unit via Declarative Automation Bundles.

---

## 1.1 Expression-Level Transformation Lineage: Reconstructing the "How" Behind Every Column

**This is what makes NEXUS Lineage unique.** Unity Catalog records column *dependency* edges — that "column A depends on column B." NEXUS Lineage goes a layer deeper: it reconstructs the **actual transformation expression** that produced each column — the precise SQL/PySpark logic (`cast`, `sum`, `concat`, `CASE`, window functions, CTE chains) — and tags each derivation with a transform category. It does this by *genuinely parsing the producing code*, then serves it as an interactive, per-column upstream drill-down.

What makes that hard — and what the engine does:

- **Expression reconstruction, not just edges.** Each column node stores the full producing expression (`meta_json.expr`) classified into ~10 categories (`window`, `aggregation`, `case`, `null_handling`, `cast`, `string_fn`, `date_fn`, `arithmetic`, `projection`, `other`) via priority-ordered matching in `graph_builder._classify_transform`. Every column→column hop carries literal logic *and* a category — not just a link.
- **Two real code parsers (no name-matching heuristics).** A `sqlparse`-based SQL parser with per-statement output attribution, `USE CATALOG/SCHEMA` qualification, alias resolution, `STREAM()` unwrapping, and balanced-paren **CTE resolution** (resolves `WITH`-clause columns back to base tables); and an **AST-based PySpark parser** that walks Python `ast` to resolve f-string FQNs, DataFrame chains (`.select/.agg/.withColumn/.withColumnRenamed/.groupBy`), DataFrame aliases, and `spark.table/read/readStream` sources.
- **Coverage across every producer type** (`transformation_lineage/extraction/pipeline.py`): JOB task runs, workspace NOTEBOOKs, Lakeflow/DLT PIPELINE libraries, declarative VIEW/MATERIALIZED_VIEW/STREAMING_TABLE definitions (`SHOW CREATE TABLE`), and ad-hoc producers via **query-history fallback** (CTAS/INSERT/MERGE with `entity_type=NULL`). See §4.5.
- **Python-defined DLT** handled where `SHOW CREATE TABLE` can't help — the AST parser detects `@dlt.table`/`@dlt.view` decorators, scopes a symbol table per dataset function, and walks the returned DataFrame chain.
- **Precise source attribution, no false fan-out.** Each derive edge pins the exact resolved source node via `meta_json.src_node_id`, so the endpoints builder joins source by node id (not column name) — preventing an output column from cross-joining to every table that shares a column name. Self-loop and duplicate-hop guards keep edges clean.
- **Honest boundary handling.** Lakehouse Federation (`table_type` FOREIGN) and Delta Sharing sources are surfaced as an explicit `external_source` skip rather than a misleading empty result; a local job that *reads* a shared/foreign table still captures it as an upstream source.

---

## 2. Directory Structure

The repository is **flat** — `backend/`, `frontend/`, and the `transformation_lineage/` engine package all sit at the top level. The build job runs the same `transformation_lineage/` package the backend imports (no separate embedded copy).

```
lineage_app/
├── backend/                         # FastAPI application
│   ├── main.py                      # App entry, routes, middleware, rate limiting, auth
│   ├── lineage_service.py           # Table/column lineage (system.access queries) + cache
│   ├── transform_service.py         # Transformation lineage read path (BFS backtrack, per-fqn run, invalidate)
│   ├── build_service.py             # Lineage Builder (job submission & polling)
│   ├── excel_export.py              # Styled .xlsx export generation
│   ├── models.py                    # Pydantic models for all API responses
│   └── tests/                       # Unit tests
├── frontend/                        # React + TypeScript SPA
│   ├── src/
│   │   ├── components/
│   │   │   ├── graph/               # ReactFlow canvas, ELK layout
│   │   │   ├── transform/           # TransformPanel, TransformCanvas, BuildProgress, PruningControls
│   │   │   ├── lineage/             # Column drill-down
│   │   │   ├── browse/ landing/ layout/ ui/
│   │   │   ├── AdminDashboard.tsx   # Ops dashboard + transformation-lineage invalidate controls
│   │   ├── api/                     # Typed fetch client (client.ts, transform.ts)
│   │   ├── store/                   # Zustand stores (lineageStore, transformStore)
│   │   └── lib/                     # Utilities, ELK worker
│   └── dist/                        # Production build (served by FastAPI; committed)
├── transformation_lineage/          # Transformation-lineage engine (imported by app AND run by build job)
│   ├── pipeline.py                  # Orchestrator (extract → version → parse → graph → reconcile → materialize → endpoints → enrich)
│   ├── config.py                    # LineageJobConfig (incl. force_reparse)
│   ├── types.py                     # Shared dataclasses
│   ├── extraction/                  # discovery, resolver, fetcher, normalize, pipeline (4 producer paths)
│   ├── parsing/                     # sql_parser (CTE/STREAM), pyspark_ast_parser (DLT), graph_builder, artifact_parser
│   ├── versioning/                  # change_detection (PARSER_VERSION + version_token)
│   ├── reconciliation/              # System lineage reconciliation stats
│   ├── materialization/             # KPI subgraph caching
│   ├── sublineage/                  # edge_endpoints_builder, backtrack, BFS, expression_enricher
│   └── storage/                     # schema.py (12 Delta tables), writers.py
├── notebooks/
│   └── run_pipeline.py              # Databricks notebook executed by build jobs (reads job widgets)
├── docs/                            # Documentation
├── monitoring/                      # Monitoring utilities
├── databricks.yml                   # DABs deployment config
├── app.yaml                         # Databricks App runtime config
├── requirements.txt                 # Python dependencies
└── setup.sql                        # SPN permission grants
```

---

## 3. Tech Stack (Self-Contained)

| Layer | Technology | Purpose |
|-------|-----------|--------|
| Backend | FastAPI + Uvicorn | Single-process async API server (64-thread pool) |
| Frontend | React 18 + TypeScript | SPA with hot-reload dev, production bundle served by FastAPI |
| Graph Rendering | ReactFlow + ELK.js | Hierarchical DAG layout in Web Worker |
| State | Zustand | Client-side store |
| Styling | Tailwind CSS | Utility-first CSS |
| Data | Databricks SDK + DBSQL | UC system tables, Statement Execution API |
| Deployment | Declarative Automation Bundles | One-command deploy to Databricks Apps |
| Auth | OAuth (on-behalf-of) | User identity via `x-forwarded-access-token` |

**No Streamlit. No D3.js iframes. No external code references.**

---

## 4. Transformation Lineage — Complete Feature Set

### 4.1 Lineage Builder (`build_service.py`)

The Lineage Builder submits serverless one-time jobs to construct transformation lineage:

- **Auto-derives** the pipeline notebook path from deployment location (override via `PIPELINE_NOTEBOOK_PATH`)
- **REST API submission** (`/api/2.1/jobs/runs/submit`) for one-time serverless jobs
- **Environment spec** pins **`sqlparse==0.4.4`** exactly (plus `requests`, `databricks-sdk`). The pin is deliberate: `sqlparse` 0.5.x tokenizes multi-statement SQL differently and leaks alias resolution across statements. (Distinct from the app's own `requirements.txt` range `sqlparse>=0.4.4,<1.0.0`.)
- **Build parameters** passed to the notebook: `TARGET_CATALOG`/`TARGET_SCHEMA` (the dedicated store — see §4.6), `KPI_TABLES` (the table being generated), `BUILD_ONLY=true`, and **`FORCE_REPARSE`**. A `force_rebuild` request sets `FORCE_REPARSE=true` so a "Regenerate" / clear-and-rebuild actually re-runs the parser even when the source content is byte-identical (otherwise change-detection early-terminates — see §6).
- **Progress polling** via SDK `get_run()` with lifecycle-to-step mapping
- **8 build steps**: Validating Table → Initializing Job → Schema Discovery → SQL Extraction → Dependency Parsing → Graph Construction → Edge Materialization → Cache Update

### 4.2 Transform Service (`transform_service.py`) — read path

- **Freshness check** — single-query COUNT+MAX (eliminates double roundtrip)
- **Per-table latest-run scoping** — reads serve a table from the latest run that actually **built that table** (`dst_fqn`), not a single global-latest run. Because each build is scoped to one table, a naive global-latest would make building table B hide table A's lineage. (`_get_latest_run_id`)
- **BFS backtracking** — walks upstream edges from target column to source columns; default depth from `TRANSFORM_MAX_DEPTH` (env, default 8). Dedups hops by `(source, target)` column pair (not `edge_id`), so a column referenced both bare and alias-qualified renders as one edge.
- **Self-loop filter** — read-side guard drops any `src_node_id == dst_node_id` edge (defends already-stored data).
- **Category resolution** — `TRANSFORM_CATEGORIES` provides the color map for the UI legend; the parser emits ~10 transform tags (see §1.1). The two vocabularies are not 1:1 — unmapped tags fall back to a neutral color.
- **Invalidate** — `clear_transform_lineage(scope)` flushes the in-memory cache and optionally wipes stored edges (per-table or global); the expensive audit-path and LLM-expression caches are retained. Exposed at `POST /api/transform/invalidate` (admin).
- **Single-flight coalescing** — bounded per-key lock pool prevents thundering herd
- **Memory-bounded TTL cache** — 64MB max, configurable TTL
- **Parallel SQL** — thread pool for concurrent edge/category queries

### 4.3 Pipeline (`transformation_lineage/pipeline.py`)

The transformation lineage pipeline:

1. **Extraction** — discover producing code across **four paths** (definition / JOB / NOTEBOOK / PIPELINE) plus a **query-history fallback** for entity-less producers, then fetch artifact source. See §4.5.
2. **Version Check** — batch comparison on a **`version_token`** = `sha256("parser_v"+PARSER_VERSION+NUL+raw_source)` (not just content SHA), so a parser upgrade re-parses unchanged sources. `force_reparse` bypasses this. See §6.
3. **Parse + Graph** — parallel (8 threads) parsing + graph construction. PySpark is parsed **AST-first** (`pyspark_ast_parser`), with the regex parser used only as a fallback when the AST yields nothing; SQL via `sql_parser` (CTE resolution, `STREAM()` unwrap, single-source unqualified-column heuristic). `graph_builder` classifies each derive edge into a transform category, stamps the exact `src_node_id` and per-write-sink `output_table_fqn`, and drops self-loops at parse time.
4. **Write Results** — deduped nodes + edges to Delta tables
5. **Reconciliation** — stats against system lineage
6. **Materialization** — KPI subgraph cache for fast serving
7. **Edge Endpoints** — build the denormalized serve table (`lineage_edge_endpoints`): one row per column→column hop, pre-joined with expression text, `expr_lang`, `transform_category`, and source path. The source is joined **by `src_node_id`** (not column name) to avoid cross-joins. See §4.6.
8. **Expression Enrichment** — best-effort LLM PySpark→SQL translation (`databricks-meta-llama-3-3-70b-instruct`), cached cross-run in `lineage_pyspark_to_sql_cache` keyed on `sha256(expr)` so each unique snippet is translated once; only `expr_lang='pyspark'` rows with NULL `expr_sql` are translated.

### 4.4 Frontend Components

| Component | File | Function |
|-----------|------|----------|
| TransformPanel | `TransformPanel.tsx` | Slide-out panel from column click. Opt-in: shows a `needs_build` state with a compute-cost warning and an explicit **Generate** action — it never auto-builds. A **persistent header build button** shows the state at a glance: enabled **Generate** (not built), amber **Regenerate** (stale), or grayed **"Lineage built"** when fresh (still force-rebuildable). |
| TransformCanvas | `TransformCanvas.tsx` | ReactFlow canvas for the transformation DAG (target on top, upstream cascading down) |
| TransformNode | `TransformNode.tsx` | Column nodes with table/depth coloring |
| TransformEdge | `TransformEdge.tsx` | Edges with persistent, zoom-stable category + expression labels |
| BuildProgress | `BuildProgress.tsx` | Real-time job progress with step DAG |
| PruningControls | `PruningControls.tsx` | **Category filter + path isolation** (the depth slider was removed — the popup always shows the column's full end-to-end lineage) |
| AdminDashboard | `AdminDashboard.tsx` | Ops dashboard + **Flush cache** / **Wipe lineage** invalidate controls (`POST /api/transform/invalidate`) |

---

### 4.5 Producer Resolution / Entity-Type Support (`extraction/`)

Transformation lineage is only as good as the engine's ability to find the *code that produced* a table. The extractor uses four resolution paths plus a fallback, so coverage spans every common producer type:

1. **Definition-based** — for `VIEW` / `MATERIALIZED_VIEW` / `STREAMING_TABLE` targets, read the object's own `SHOW CREATE TABLE` and parse it directly. No discovery, no system-table lineage lag. Per-catalog `information_schema` is used to read `table_type`; `FOREIGN` (Lakehouse Federation / Delta Sharing) is recorded as an `external_source` skip rather than a misleading empty result. (`extraction/pipeline.py:_extract_object_definitions`)
2. **JOB run tasks** — resolve a discovered job run to its tasks: notebook tasks, `spark_python_task`, and `sql_task` SQL files. (`extraction/resolver.py`)
3. **NOTEBOOK entities** — resolve a notebook `entity_id` to its workspace path via a cached `system.access.audit` scan (Delta `lineage_notebook_path_cache`), then export the source.
4. **PIPELINE / DLT** — resolve a Lakeflow/DLT pipeline via `pipelines.get(...).spec.libraries` to its notebook/file libraries. Python-defined DLT (`@dlt.table` / `@dlt.view`) is handled by the AST parser where `SHOW CREATE TABLE` cannot help.
5. **Query-history fallback** — tables with **no tracked producing entity** (ad-hoc SQL / SQL editor / scripts → `entity_type=NULL` in `column_lineage`) are recovered from `system.query.history`: the latest FINISHED write statement whose parsed output equals the target is parsed. *Identity-scoped* — the build SP only sees query history it has visibility into; otherwise the table degrades to a `no_producing_query` skip. (`extraction/pipeline.py:_extract_from_query_history`)

### 4.6 Dedicated Lineage Store (Option A)

All transformation-lineage Delta tables live in one app-SP-owned schema, `LINEAGE_CATALOG.LINEAGE_SCHEMA` (env vars; default `lattice_lineage.lineage` — **override per deployment**, see §8). Node ids embed the real data catalog (`col:<catalog>.<schema>.<table>::<col>`), so the build SP needs **CREATE/MODIFY only on the dedicated store — zero write on any data catalog**.

The store has **12 Delta tables** (`storage/schema.py`): `lineage_nodes`, `lineage_edges`, `lineage_edge_endpoints` (the serve table), `lineage_raw_code`, `lineage_code_versions`, `lineage_parse_metrics`, `lineage_graph_cache`, `lineage_reconciliation`, `lineage_extraction_reports`, `lineage_sublineage_cache`, `lineage_notebook_path_cache`, and `lineage_pyspark_to_sql_cache`. A global **Wipe lineage** clears the lineage tables (including `code_versions`, so rebuilds fully re-parse) but retains the two expensive caches (`notebook_path_cache`, `pyspark_to_sql_cache`).

---

## 5. API Endpoints

### Table/Column Lineage
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tables` | All tables (cached, client filters) |
| GET | `/api/catalogs` | Available catalogs |
| GET | `/api/schemas?catalog=` | Schemas in catalog |
| GET | `/api/lineage?catalog=&schema=` | Table-level DAG |
| GET | `/api/lineage/trace?table=` | End-to-end cross-catalog trace |
| GET | `/api/columns?catalog=&schema=&table=` | Lazy column load |
| GET | `/api/column-lineage?...` | Column-level edges |
| GET | `/api/schema-column-lineage?...` | All column edges for schema |
| GET | `/api/lineage/export?...` | Excel export (.xlsx) |

### Transformation Lineage
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/transform/freshness?...` | Staleness check for table |
| POST | `/api/transform/build` | Submit build job (`force_rebuild` ⇒ force re-parse) |
| GET | `/api/transform/status/{run_id}` | Poll build progress |
| GET | `/api/transform/trace?...` | BFS backtrack column |
| GET | `/api/transform/categories` | Category→color mapping |
| GET | `/api/transform/build-configured` | Check if pipeline is configured |
| POST | `/api/transform/invalidate?scope=cache\|table\|all` | Invalidate transform cache / wipe stored lineage (admin) |

### Delta Sharing
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sharing/overlay?...` | Sharing lens for graph |
| GET | `/api/sharing/overview` | Metastore-wide sharing inventory |

### Admin & System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/diagnostics` | Deploy self-check |
| GET | `/api/user-info` | Current user + admin status |
| GET | `/api/admin/status` | P50/P95/P99, memory, cache |
| POST | `/api/cache/invalidate` | Clear all caches (admin) |
| POST | `/api/admin/evict-cache?key=` | Evict specific key (admin) |
| GET | `/api/entity-name?...` | Resolve job/pipeline display name |

---

## 6. Performance Optimizations

| Optimization | Location | Impact |
|-------------|----------|--------|
| Single-flight coalescing | `lineage_service.py`, `transform_service.py` | Prevents thundering herd on cache miss |
| Memory-bounded LRU/TTL cache | `lineage_service.py` (250MB), `transform_service.py` (64MB) | O(1) lookups, bounded memory |
| ThreadPoolExecutor (64 workers) | `main.py` lifespan | Concurrent SDK/SQL blocking calls |
| Parallel pipeline parsing (8 threads) | `pipeline.py` | 8x throughput on multi-artifact builds |
| Batch version checking | `versioning/change_detection.py` | Single SQL query vs N queries |
| Early termination + parser versioning | `pipeline.py`, `versioning/change_detection.py` | Skip parse/graph when the **`version_token`** is unchanged. The token folds in `PARSER_VERSION` (currently `5`; history 1–5), so a parser upgrade forces a one-time re-parse of unchanged sources. `content_sha256` is kept separately as code provenance. A `FORCE_REPARSE` build bypasses early termination. |
| Node deduplication | `pipeline.py` Phase 4 | Prevents duplicate writes |
| Lightweight size estimation | `transform_service.py` | 10-50x faster than JSON serialization |
| Pushdown predicates | `transform_service.py` | Avoids full table scans |
| Pre-indexed adjacency maps | `transform_service.py` BFS | O(1) neighbor lookup |
| Per-user rate limiting | `main.py` RateLimitMiddleware | Protects SQL warehouse |
| Prefetch cost cache | `main.py` lifespan | First load shows costs immediately |

---

## 7. Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| Missing PIPELINE_NOTEBOOK_PATH | Auto-derived from `__file__`; graceful 503 if unresolvable |
| SQL warehouse unavailable | Sanitized error message, no internal paths leaked |
| Empty lineage (no edges) | Returns `has_lineage: false`, UI shows "No transformation logic found" state |
| Transformation lineage not built | Panel enters `needs_build` — explicit opt-in Generate with a compute-cost warning; never auto-builds |
| Stale transformation lineage | Header button shows amber **Regenerate**; force_rebuild re-parses |
| Mis-resolved alias → self-loop edge | Dropped at parse time (`graph_builder`) and again at read time (`src_node_id != dst_node_id`) |
| Same-named column across source tables | No false fan-out — endpoints builder joins source by `src_node_id`, not column name |
| Entity-less producer (ad-hoc SQL) | Query-history fallback; degrades to `no_producing_query` skip if the SP lacks query-history visibility |
| External/shared source (FOREIGN / Delta Sharing) | Recorded as `external_source` skip, surfaced in the panel; not a misleading empty graph |
| Column is a source (no upstream) | `is_source_column: true` flag, distinct UI state |
| Max depth reached in BFS | Stops at configured limit, reports `max_depth_reached` |
| Concurrent build requests | Freshness check prevents redundant jobs |
| Build job failure | `BuildJobStatus.is_success=false` with state_message |
| Orphaned polling (panel closed mid-build) | Triple guard: `buildPolling` + `panelState !== 'closed'` checked at poll entry, post-await, and pre-schedule |
| Double-open race (rapid column clicks) | Staleness guard compares `selectedTable/Column` after each await; aborts if superseded |
| Rate limit exceeded | 429 with clear message; per-user (not per-IP) |
| Graph too large (catalog-wide) | 413 with actionable message |
| Path traversal attacks | `os.path.realpath` + prefix validation on static files |
| SQL injection | `_IDENTIFIER_RE` regex validates all user inputs |
| Token abuse | SHA256 hashed tokens for rate-limit keys; LRU-bounded cache |
| Non-admin accessing admin APIs | 403 with identity check via `x-forwarded-access-token` |
| Startup failures | Performance patches are non-fatal; app continues without them |

---

## 8. Deployment

```bash
# Deploy to dev
databricks bundle deploy -t dev --profile <profile> --var warehouse_id=<id>
databricks bundle run lineage-explorer -t dev --profile <profile>

# Deploy to prod
databricks bundle deploy -t prod --profile <profile> --var warehouse_id=<id>
```

### Required Grants (run as metastore admin)
```sql
GRANT BROWSE ON CATALOG <catalog> TO `<app-spn>`;
GRANT USE CATALOG ON CATALOG system TO `<app-spn>`;
GRANT USE SCHEMA ON SCHEMA system.access TO `<app-spn>`;
GRANT SELECT ON TABLE system.access.table_lineage TO `<app-spn>`;
GRANT SELECT ON TABLE system.access.column_lineage TO `<app-spn>`;
-- Transformation lineage producer resolution:
GRANT SELECT ON TABLE system.access.audit TO `<app-spn>`;        -- notebook path resolution
GRANT SELECT ON TABLE system.query.history TO `<app-spn>`;        -- query-history fallback (account-admin grant; identity-scoped)
-- Dedicated lineage store (Option A): the build SP owns ONE schema and needs no write on data catalogs:
GRANT ALL PRIVILEGES ON SCHEMA <LINEAGE_CATALOG>.<LINEAGE_SCHEMA> TO `<app-spn>`;
```
> **Option A:** transformation-lineage tables are written only to `LINEAGE_CATALOG.LINEAGE_SCHEMA`; node ids embed the real data catalog, so the build SP needs **zero write** on any data catalog. `system.query.history` is identity-scoped — without account-admin-level visibility the query-history fallback only sees the SP's own queries, so entity-less tables produced by other users won't resolve.

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `DATABRICKS_WAREHOUSE_ID` | — | SQL Warehouse ID (required) |
| `LINEAGE_CATALOG` | `lattice_lineage` | Catalog for the dedicated transform-lineage store. **Override per deployment** (`--var lineage_catalog=...`) — the default rarely exists; if wrong, every transform read fails. |
| `LINEAGE_SCHEMA` | `lineage` | Schema within `LINEAGE_CATALOG` for the store |
| `PIPELINE_NOTEBOOK_PATH` | (auto-derived) | Path to `run_pipeline` notebook the build job runs (must be readable by the app SP) |
| `LINEAGE_WINDOW_DAYS` | `365` | Max producer staleness before table-lineage drops off (paired with `event_date` partition pruning) |
| `CACHE_TTL_SECONDS` | `28800` | Main lineage cache TTL |
| `CACHE_MAX_MEMORY_MB` | `250` | Max memory for lineage cache |
| `TRANSFORM_CACHE_TTL_SECONDS` | `3600` | Transform cache TTL |
| `TRANSFORM_MAX_DEPTH` | `8` | Max BFS depth for the transform backtrack |
| `BUILD_CACHE_TTL_HOURS` | `24` | Hours before lineage is stale |
| `ADMIN_GROUP_NAME` | `admins` | Group for admin access |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Max requests/user/window |

> **Build-job parameters** (set by `build_service.py`, read by `notebooks/run_pipeline.py`) are separate from app env vars: `TARGET_CATALOG`/`TARGET_SCHEMA`, `KPI_TABLES`, `BUILD_ONLY`, `FORCE_REPARSE`, `DISCOVERY_LOOKBACK_HOURS` (default 1080 = 45 days), `SRC_PATH`. Entity types discovered: `JOB, NOTEBOOK, PIPELINE`.

---

## 9. Security

- **Input validation**: All identifiers validated against `^[A-Za-z0-9_]{1,255}
- **CSP headers**: `frame-ancestors 'none'`, strict `script-src`
- **Path traversal protection**: `os.path.realpath` + prefix check
- **Rate limiting**: Per-user (token-hashed), LRU-bounded at 10K users
- **Admin gating**: Group membership check via user's own OAuth token
- **Error sanitization**: Internal paths/SQL never exposed in API responses
- **No row data access**: App reads only metadata + system tables
