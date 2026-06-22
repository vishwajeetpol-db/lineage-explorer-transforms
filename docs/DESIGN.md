# LATTICE Explorer — Unified Design Document

## Overview

LATTICE Explorer combines two lineage capabilities into a single app with a **macro → micro** interaction model:

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

The panel follows this state machine:

```
closed → loading-freshness → freshness-loaded → [building] → loading-trace → trace-loaded
                                                                               └→ no-lineage
```

#### What happens under the hood:

1. **Freshness Check** (`GET /api/transform/freshness`)
   - Queries the `lineage_edge_endpoints` Delta table
   - Returns: exists? edge_count? last_built? is_stale?

2. **If fresh** → jumps directly to step 4

3. **If stale or missing** → shows "Build Lineage" button (or auto-builds)
   - `POST /api/transform/build` submits a **serverless one-time job** that runs the `run_all` notebook
   - The notebook executes the full LATTICE pipeline:
     1. Discovery — find runs touching the KPI table in `system.access.column_lineage`
     2. Resolution — expand to task-level sources via Jobs API
     3. Extraction — fetch notebook source code (Workspace Export + GitHub)
     4. Parsing — `sqlparse` for SQL, regex for PySpark (`.withColumn`, `.select`, `spark.table`)
     5. Graph Construction — build nodes + edges
     6. Storage — append to 8 Delta tables
     7. Materialization — BFS subgraph expansion, write to `lineage_edge_endpoints`
     8. Cache Update
   - The panel shows an **8-step DAG progress bar** polling `GET /api/transform/status/{run_id}` every 3s

4. **Trace Fetch** (`GET /api/transform/trace?catalog=...&schema=...&table=...&column=...&max_depth=N`)
   - Backend performs **BFS backtracking** from the target column through the edge table
   - Returns layered levels (depth 0 = target, depth N = Nth upstream source)
   - Each edge carries: `expression`, `category`, `source_file`
   - `max_depth` parameter controls how many levels to traverse (default 8)

5. **Render** — The TransformCanvas (React Flow sub-graph) shows:
   - Column nodes colored by depth level (red target → blue → purple → green upstream)
   - Animated edges colored by **transformation category** (ARITHMETIC, WINDOW, AGGREGATE, TYPE CAST, FILTER, JOIN, etc.)
   - **Hover on any edge** → tooltip shows the actual expression (`COALESCE(a, b)`, `SUM(amount) OVER (...)`) and the source notebook path
   - Depth badge on each node showing how many hops from target

---

## Pruning & Filtering

The transformation graph can be complex (dozens of columns, many categories). Three interactive controls allow users to focus on what matters:

### 1. Depth Slider

A range input (1–8) in the panel toolbar. Dragging it **re-fetches from the backend** with a smaller `max_depth`, reducing the BFS traversal. This is a true data prune — fewer levels mean fewer nodes and edges returned.

**Use case:** "I only care about the immediate upstream transform, not the full 6-level chain."

### 2. Category Filter

Color-coded chips for each transform category present in the current graph (ARITHMETIC, WINDOW, AGGREGATE, JOIN, etc.). Clicking a chip **hides/shows** all edges of that category **client-side** — no re-fetch needed. Hidden edges become nearly invisible (opacity 0.08). Nodes orphaned by hidden edges remain visible but dimmed.

**Use case:** "There are 20 PASSTHROUGH edges cluttering the graph. Hide them to see only the WINDOW and AGGREGATE logic."

### 3. Path Isolation (Click-to-Focus)

Clicking any **upstream node** highlights only the edges and nodes on the path(s) between that node and the target (depth 0). Everything else dims to 15% opacity. Click the **target node** (or the ✕ badge in the toolbar) to clear isolation.

**Algorithm:** Bidirectional BFS — find intersection of (ancestors of target) ∩ (descendants of clicked node). Only nodes in both sets remain bright.

**Use case:** "I see 4 branches feeding into my KPI column. I only want to trace how `exchange_rate` contributes."

### Combining Controls

All three controls compose naturally:
- Set depth=3 → only 3 levels shown
- Hide PASSTHROUGH → clears noise edges
- Click a node → isolates one specific flow within those 3 levels

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
│  [Freshness: 2h ago ●]  [Rebuild 🔄]  [Close ✕]                       │
│                                                                         │
│  ┌──────── PRUNING TOOLBAR ─────────────────────────────────────────┐  │
│  │  Depth: ──●──────── [3]       [Path isolated ✕]                   │  │
│  │  [ARITHMETIC] [WINDOW] [̶P̶A̶S̶S̶T̶H̶R̶O̶U̶G̶H̶] [JOIN]  [All][None]       │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  TRANSFORMATION DAG (vertical, bottom-to-top)                    │   │
│  │                                                                   │   │
│  │  Depth 3:  [raw_amount]   [exchange_rate] ← click to isolate    │   │
│  │                │ ARITHMETIC    │ JOIN                             │   │
│  │                ▼               ▼                                  │   │
│  │  Depth 2:     [converted_amount]                                 │   │
│  │                │ WINDOW: SUM(...) OVER (PARTITION BY region)      │   │
│  │                ▼                                                  │   │
│  │  Depth 1:  [regional_total]                                      │   │
│  │                │ TYPE CAST: CAST(... AS DECIMAL(18,2))            │   │
│  │                ▼                                                  │   │
│  │  Depth 0:  ◉ col_b  (TARGET) ← click to clear isolation         │   │
│  │                                                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  [12 columns • 9 transforms • 3 levels deep • 45ms]                   │
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

Category filtering hides edges that are already loaded — it's a visual prune, not a data prune. Re-fetching would be wasteful since the backend BFS doesn't support category exclusion (all edges at depth ≤ N are returned regardless of category). Client-side toggle provides instant feedback (<16ms frame).

Depth changes DO re-fetch because they reduce the BFS traversal scope — genuinely fewer rows returned.

---

## Architecture Layers

```
Frontend (React 18 + TypeScript)
├── Landing/Search (browse UC catalogs)
├── Table Lineage Canvas (React Flow + ELK.js)
│   └── TableNode → column list → 🔬 icon
├── Transform Panel (slide-out, Framer Motion)
│   ├── PruningControls (depth slider, category chips, isolation badge)
│   ├── FreshnessBadge
│   ├── BuildProgress (8-step DAG)
│   └── TransformCanvas (React Flow sub-graph, filtering + path isolation)
└── Zustand stores: lineageStore + transformStore (pruning state)

Backend (FastAPI + Uvicorn, 64 threads)
├── /api/lineage/*       → lineage_service.py (UC system tables)
├── /api/transform/*     → transform_service.py (Delta tables + BFS)
├── /api/transform/build → build_service.py (Jobs API)
├── /api/sharing/*       → sharing overlay
├── /api/admin/*         → ops dashboard
└── Shared infra: cache.py, auth.py, rate_limit.py, security.py

Data Layer
├── UC System Tables (read-only, no writes)
│   ├── system.access.table_lineage
│   ├── system.access.column_lineage
│   └── system.billing.usage
├── Transform Delta Tables (written by build job)
│   └── lattice_lineage.lineage.lineage_edge_endpoints
└── Serverless Jobs (build pipeline, on-demand)
    └── run_all notebook (unchanged from LATTICE)
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
- `PIPELINE_NOTEBOOK_PATH` — workspace path to `run_all` notebook (must be set per workspace)
- `TRANSFORM_CACHE_TTL_SECONDS` — transform cache TTL (default: 3600)
- `BUILD_CACHE_TTL_HOURS` — hours before lineage is considered stale (default: 24)
