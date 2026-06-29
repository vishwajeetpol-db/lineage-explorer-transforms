# NEXUS Lineage — Unified Design Document

## Overview

NEXUS Lineage combines two lineage capabilities into a single app with a **macro → micro** interaction model:

| Zoom Level | Source | What You See |
| --- | --- | --- |
| 1. Table DAG | UC `system.access.table_lineage` | Full table-to-table graph with pipeline/job nodes, cost badges, Delta Sharing overlay |
| 2. Column Edges | UC `system.access.column_lineage` | Which columns flow between tables (system-recorded) |
| 3. Transformation Graph | Custom-parsed Delta tables (`lineage_edge_endpoints`) | Actual SQL/PySpark expressions, categories, source files for each column derivation |

---

## User Journey: Pipeline → Column Transformation Lineage

### Step 1 — Land on a Table (Macro View)

The user arrives at the app and either:
- Searches for a table via the global search bar (Cmd+K)
- Browses Catalog → Schema → selects a table
- Deep-links via `?table=catalog.schema.table`

This triggers a **cross-catalog trace** (`/api/lineage/trace`) that walks `system.access.table_lineage` recursively in both directions. The result is a full DAG rendered on the React Flow canvas showing:
- **Table nodes** (blue/green/amber by type)
- **Entity nodes** (pipelines, jobs, notebooks as intermediate vertices)
- **Cost badges** (30-day serverless spend from `system.billing`)
- **Delta Sharing overlay** (shared-out / shared-in boundaries)

At this level, the user sees the **entire data pipeline** — every upstream source and downstream consumer.

### Step 2 — Enable Column Mode (Meso View)

The user toggles **Column Lineage** in the toolbar. Table nodes become expandable — clicking one reveals its columns inline on the graph node.

When a column is clicked:
- It's highlighted in purple
- UC column-level edges (`system.access.column_lineage`) light up across the graph
- The user sees which columns flow into/out of this column at the **table boundary level**

This answers: *"Which source columns feed into my target column?"* — but NOT how.

### Step 3 — Drill Into Transformations (Micro View)

On the selected (purple-highlighted) column, a **🔬 Microscope icon** appears. Clicking it opens the **Transformation Panel** — a slide-out canvas from the right edge.

The panel follows this state machine (`transformStore.ts`):

```
closed → loading → needs_build → building → ready
                      │                       └→ (source column / no transformation logic)
                      └→ (fresh) ──────────────→ ready
   any → error
```

`needs_build` is the **opt-in gate**: when lineage is missing or stale the panel shows a compute-cost warning and an explicit **Generate** button — it **never auto-builds**. A persistent header button reflects state at a glance (Generate / amber Regenerate / grayed "Lineage built", still force-rebuildable).

#### What happens under the hood:

1. **Freshness Check** (`GET /api/transform/freshness`) — queries the serve table; returns exists / edge_count / last_built / is_stale.

2. **If fresh** → jumps directly to step 4.

3. **If stale or missing** → enters `needs_build`; on explicit **Generate**, `POST /api/transform/build` submits a **serverless one-time job** running the `run_pipeline` notebook (`force_rebuild` ⇒ `FORCE_REPARSE`, re-parsing even unchanged source). The pipeline:
   1. **Extraction** — resolve the producing code across four paths — declarative **definitions** (`SHOW CREATE TABLE` for view/MV/streaming table), **JOB** task runs (notebook / `spark_python_task` / `sql_task`), **NOTEBOOK** entities (audit-log path lookup), **PIPELINE/DLT** libraries — plus a **query-history fallback** for entity-less producers (`entity_type=NULL`).
   2. **Version check** — skip artifacts whose `version_token` (folds in `PARSER_VERSION`) is unchanged, unless `FORCE_REPARSE`.
   3. **Parse + Graph** — SQL via `sqlparse` (CTE resolution, `STREAM()` unwrap, alias/qualification); PySpark **AST-first** (regex only as fallback). Each derive edge is classified into a transform category and pins its exact `src_node_id`.
   4. **Storage** — write nodes/edges to the dedicated 12-table store.
   5. **Materialization + Edge Endpoints** — build the serve table (`lineage_edge_endpoints`); source joined by `src_node_id` (no column-name cross-join).
   6. **Expression enrichment** — best-effort LLM PySpark→SQL, cached per unique snippet.
   - The panel shows an **8-step DAG progress bar** polling `GET /api/transform/status/{run_id}` every 3s.

4. **Trace Fetch** (`GET /api/transform/trace?catalog=...&schema=...&table=...&column=...`)
   - Backend performs **BFS backtracking** from the target column through the serve table, scoped to the latest run that built **that** table (`dst_fqn`), self-loops filtered, hops deduped by `(src,dst)` pair.
   - Returns layered levels (depth 0 = target, depth N = Nth upstream source); each edge carries `expression`, `category`, `source_file`.

5. **Render** — The TransformCanvas (React Flow sub-graph) shows:
   - Column nodes colored by depth level (red target on top → upstream cascading down)
   - Edges colored by **transformation category** (the parser emits: `window, aggregation, case, null_handling, cast, string_fn, date_fn, arithmetic, projection, other`)
   - **Persistent edge labels** show the actual expression (`concat('CH-', channel)`, `sum(quantity*unit_price)`) + category, zoom-stable (no hover needed)

---

## Pruning & Filtering

The popup always shows the selected column's **full end-to-end** transformation lineage (there is no depth knob — depth isn't a meaningful choice when inspecting one column's derivation; the earlier depth slider was removed). Two context-relevant controls (`PruningControls.tsx`) let users focus:

### 1. Category Filter

Color-coded chips for each transform category present in the current graph (cast, aggregation, string_fn, window, …). Clicking a chip **hides/shows** all edges of that category **client-side** — no re-fetch. Hidden edges fade out; orphaned nodes dim.

**Use case:** "Hide the projection passthroughs to see only the aggregation and window logic."

### 2. Path Isolation (Click-to-Focus)

Clicking any **upstream node** highlights only the edges and nodes on the path(s) between that node and the target. Everything else dims. Click the target node (or the ✕ badge) to clear.

**Algorithm:** Bidirectional BFS — intersection of (ancestors of target) ∩ (descendants of clicked node).

**Use case:** "I see 4 branches feeding into my KPI column. I only want to trace how `exchange_rate` contributes."

### Combining Controls

The two controls compose: hide a category to clear noise, then click a node to isolate one specific flow. Opening a new column resets all pruning state; the category filter has All/None bulk toggles.

Resetting: opening a new column resets all pruning state. The "All/None" buttons on the category filter provide bulk toggle.

---

## Interaction Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│  LANDING PAGE                                                           │
│  [Search: catalog.schema.table]  or  [Browse Catalogs]                  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ select table
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  TABLE LINEAGE CANVAS (React Flow + ELK.js)                             │
│                                                                         │
│  ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐            │
│  │ source  │───▶│ pipeline │───▶│ target  │───▶│consumer │            │
│  │ table   │    │ (entity) │    │ table   │    │ table   │            │
│  └─────────┘    └──────────┘    └────┬────┘    └─────────┘            │
│                                      │                                  │
│                          [Toggle: Column Lineage ON]                    │
│                                      │ expand node                      │
│                                      ▼                                  │
│                              ┌──────────────┐                           │
│                              │ target table │                           │
│                              │ ─────────── │                           │
│                              │ • col_a     │                           │
│                              │ • col_b  ◀── selected (purple)          │
│                              │ • col_c  🔬 │ ← click microscope        │
│                              └──────┬───────┘                           │
└─────────────────────────────────────┼───────────────────────────────────┘
                                      │ opens panel
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  TRANSFORMATION PANEL (slide-in from right)                             │
│                                                                         │
│  TRANSFORMATION LINEAGE   [⚡ Generate / ✓ Lineage built]   [Close ✕]  │
│                                                                         │
│  ┌──────── CONTROLS ────────────────────────────────────────────────┐  │
│  │  ⧉ filter:  [cast] [aggregation] [string_fn] [window]  [All][None]│  │
│  │             [Path isolated ✕]                                      │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  TRANSFORMATION DAG (vertical, target on top)                    │   │
│  │                                                                   │   │
│  │  ◉ col_b  (TARGET) ← click to clear isolation                    │   │
│  │                ▲                                                  │   │
│  │                │ cast: cast(order_ts AS date)                     │   │
│  │  [order_date]                                                     │   │
│  │                ▲                                                  │   │
│  │                │ aggregation: sum(quantity*unit_price)            │   │
│  │  [net_revenue]   [exchange_rate] ← click to isolate              │   │
│  │                                                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  [12 columns • 9 transforms • full lineage • 45ms]                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### Why two separate caches?

| Cache | TTL | Max Size | Rationale |
| --- | --- | --- | --- |
| Table/Column lineage | 8h | 250MB | UC system tables change infrequently |
| Transform lineage | 1h | 64MB | User-triggered builds update data more often |

Both use single-flight coalescing — if 10 users click the same column simultaneously, only 1 SQL query fires.

### Why an async build job (not real-time parsing)?

Source code parsing is expensive (fetch notebooks via API, parse SQL/PySpark, resolve joins). Running this at query time would block the UI for 2-5 minutes. Instead:
- Build runs as a **serverless one-time job** (no persistent cluster cost)
- Results are **materialized to Delta** (durable, queryable, cacheable)
- Subsequent views of the same table hit cache in <50ms
- Freshness gating (24h default) prevents unnecessary rebuilds

### Why the microscope icon (not auto-open)?

UC column lineage (step 2) is instant — it reads from system tables. Transformation lineage (step 3) may require a build job (minutes). Separating the two preserves the "instant feedback" feel of the table graph while making the deeper insight opt-in.

### Why client-side category filtering (not re-fetch)?

Category filtering hides edges that are already loaded — it's a visual prune, not a data prune. The full per-column lineage is fetched once; toggling categories or isolating a path is instant (<16ms frame) with no extra query. (There is no depth control — the popup always loads the column's complete end-to-end lineage.)

### Why pin the source by node id (not column name)?

A whole notebook is parsed as one artifact sharing a single transformation node. If the serve table matched a derive edge's source by *column name*, an output column would cross-join to **every** source table that happens to expose a same-named column (e.g. `customer_id` in several tables). Each derive edge therefore records its exact resolved `src_node_id`, and the endpoints builder joins on that — so the popup shows only the real source.

---

## Architecture Layers

```
Frontend (React 18 + TypeScript)
├── Landing/Search (browse UC catalogs)
├── Table Lineage Canvas (React Flow + ELK.js)
│   └── TableNode → column list → 🔬 icon
├── Transform Panel (slide-out, Framer Motion)
│   ├── header build button (Generate / Regenerate / grayed "Lineage built")
│   ├── PruningControls (category chips + path-isolation badge — no depth slider)
│   ├── BuildProgress (8-step DAG)
│   └── TransformCanvas (React Flow sub-graph, filtering + path isolation)
├── AdminDashboard (ops metrics + Flush cache / Wipe lineage)
└── Zustand stores: lineageStore + transformStore

Backend (FastAPI + Uvicorn, 64 threads)
├── /api/lineage/*       → lineage_service.py (UC system tables)
├── /api/transform/*     → transform_service.py (per-fqn read, BFS, invalidate)
├── /api/transform/build → build_service.py (Jobs API, force_reparse)
├── /api/sharing/*       → sharing overlay
├── /api/admin/*         → ops dashboard
└── Cross-cutting (auth, cache, rate-limiting, security) lives in main.py + lineage_service.py

Data Layer
├── UC System Tables (read-only, no writes)
│   ├── system.access.table_lineage / column_lineage / audit
│   ├── system.query.history          (transformation query-history fallback)
│   └── system.billing.usage
├── Dedicated transformation-lineage store — Option A (written by build job)
│   └── LINEAGE_CATALOG.LINEAGE_SCHEMA — 12 Delta tables
│       (serve: lineage_edge_endpoints; + nodes/edges/raw_code/code_versions/
│        parse_metrics/graph_cache/reconciliation/extraction_reports/
│        sublineage_cache/notebook_path_cache/pyspark_to_sql_cache)
└── Serverless Jobs (build pipeline, on-demand)
    └── run_pipeline notebook
```

---

## Deployment

Deployed via **Declarative Automation Bundles** (`databricks.yml`) with two targets:

| Target | Mode | Differences |
| --- | --- | --- |
| dev | development | Higher rate limits (200/min), app name suffix |
| prod | production | Standard rate limits (60/min) |

Key environment variables for the transformation feature:
- `LINEAGE_CATALOG` — catalog holding transform Delta tables (default: `lattice_lineage`)
- `LINEAGE_SCHEMA` — schema within that catalog (default: `lineage`)
- `PIPELINE_NOTEBOOK_PATH` — workspace path to `run_pipeline` notebook (must be set per workspace)
- `TRANSFORM_CACHE_TTL_SECONDS` — transform cache TTL (default: 3600)
- `BUILD_CACHE_TTL_HOURS` — hours before lineage is considered stale (default: 24)
