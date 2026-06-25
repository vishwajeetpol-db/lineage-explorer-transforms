<!-- Full reference for Lineage Explorer. The top-level README.md is the concise
     entry point; this document holds the deep-dive sections (deploy, permissions,
     architecture, caching, cost, configuration, API, troubleshooting). -->

```
    __    _                                ______           __
   / /   (_)___  ___  ____ _____ ____     / ____/  ______  / /___  ________  _____
  / /   / / __ \/ _ \/ __ `/ __ `/ _ \   / __/ | |/_/ __ \/ / __ \/ ___/ _ \/ ___/
 / /___/ / / / /  __/ /_/ / /_/ /  __/  / /____>  </ /_/ / / /_/ / /  /  __/ /
/_____/_/_/ /_/\___/\__,_/\__, /\___/  /_____/_/|_/ .___/_/\____/_/   \___/_/
                         /____/                   /_/

  Interactive DAG Visualization for Unity Catalog Lineage



  ╔═══════════════════════════════════════════════════════════════════╗
  ║  Table + Pipeline Lineage                                       ║
  ╚═══════════════════════════════════════════════════════════════════╝

  ┌───────────────┐          ┌─────────────┐          ┌─────────────────┐
  │  bronze.raw   │ ───────> │   ETL Job   │ ───────> │  silver.orders  │
  └───────────────┘          │  ● ran today│          └────────┬────────┘
                             └─────────────┘                   │
                                                               │
  ┌───────────────┐          ┌─────────────┐                   │
  │  bronze.logs  │ ───────> │     DLT     │ ─────────────────>│
  └───────────────┘          │  Pipeline   │                   │
                             │  ○ stale    │                   │
                             └─────────────┘                   │
                                                               v
                             ┌─────────────┐          ┌─────────────────┐
                             │  Scheduled  │ ───────> │  gold.summary   │
                             │     Job     │          └─────────────────┘
                             │  ● ran today│
                             └─────────────┘

  Legend
  ──────
  Pipeline freshness tells you whether the ETL process that moves
  your data is current or behind schedule. At a glance you can spot
  which pipelines need attention without opening a separate monitoring tool.

  ● green  = pipeline ran today (data is fresh)
  ○ amber  = pipeline is stale (hasn't run recently — investigate)

  Edge colors show what type of relationship you are looking at:
  ── indigo  = table-to-table lineage (data flow between tables)
  ── orange  = pipeline scope (which job/pipeline moves the data)



  ╔═══════════════════════════════════════════════════════════════════╗
  ║  Column-Level Lineage  (click any column to trace its flow)     ║
  ╚═══════════════════════════════════════════════════════════════════╝

  bronze.raw                silver.orders               gold.summary
  ┌───────────────┐         ┌─────────────────┐         ┌─────────────────┐
  │               │         │                 │         │                 │
  │  cust_id    ──┼────────>│  customer_id  ──┼────────>│  customer_id    │
  │               │         │                 │         │                 │
  │  amount     ──┼────────>│  order_total  ──┼────────>│  total_spend    │
  │               │         │                 │         │                 │
  │  state      ──┼────────>│  region       ──┼────────>│  region         │
  │               │         │                 │         │                 │
  └───────────────┘         └─────────────────┘         └─────────────────┘

  Legend
  ──────
  Column lineage traces the exact path a value takes from source to
  target — across every transformation step. Click any column in the
  UI to see its full upstream and downstream flow highlighted in purple.

  ── purple  = column-level lineage edge
  Traced from real UC edges in system.access.column_lineage.
  Zero false positives — no name-matching heuristics.



  4,000 users.  1 SQL query.  Zero code to deploy.
```

# Lineage Explorer

**Interactive DAG visualization for Unity Catalog lineage — table dependencies, column-level data flow, and pipeline visibility across schemas.**

![Tech Stack](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white) ![React](https://img.shields.io/badge/React-18-blue) ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white) ![Databricks](https://img.shields.io/badge/Databricks_Apps-FF3621?style=flat&logo=databricks&logoColor=white) ![ELK.js](https://img.shields.io/badge/ELK.js-layout-orange)

---

## Why This Exists

Unity Catalog captures lineage from every SQL operation. But exploring that lineage means writing SQL against system tables. Lineage Explorer turns those system tables into an interactive, visual DAG — deployed in one command, shared across your entire workspace.

### Key Highlights

| What | Why It Matters |
|---|---|
| **4,000 users, 1 query** | Request coalescing means thousands of simultaneous users generate a single DBSQL query. Everyone else gets the cached result instantly. |
| **Zero-code deploy** | `databricks bundle deploy` + `--var warehouse_id=<id>`. No files to edit, no config to write. One command, any workspace. |
| **Deep link integration** | Add `?table=catalog.schema.table` to the URL. Embed lineage links in any existing dashboard (Azure App Service, internal tools) with a single `<a href>` tag. |
| **Column-level lineage, zero false positives** | Traces column flow from real UC edges, not name-matching heuristics. If UC captured it, it shows up. If not, it doesn't. |
| **Select anything → full end-to-end lineage** | Pick any table (search or browse) and the app automatically traces its *complete* lineage cone — every upstream source back to every downstream target — **across all catalogs and schemas**, with the mediating pipeline/job nodes. No "trace" button to press; the only knob is the view mode. Directional BFS keeps it scoped to the object's own cone (shared hub tables don't fan out into unrelated graphs). |
| **Delta Sharing, always in the picture** | If a table's lineage touches Delta-Shared data, it just shows: shared-in sources get a "shared-in" badge and a dashed PROVIDER boundary node; shared-out tables show which share/recipient they feed. No toggle — sharing is part of lineage. The trace stops honestly at the metastore boundary (we can't read the other account). |
| **Cross-schema lineage** | Tables from other schemas/catalogs appear as cyan dashed-border nodes with full column metadata fetched from their `information_schema`. No data is assumed — only what UC captured. |
| **Smart caching, zero warehouse waste** | 8-hour TTL (configurable) for lineage data, 24-hour TTL for serverless list price, 5-minute TTL for user identity. Memory-bounded LRU cache (250MB default, configurable) auto-evicts least-used entries so the app never hits OOM. Serverless price pre-fetched at startup so first lineage load isn't delayed. |
| **Live query mode** | Admins can bypass cache and query system tables directly for the freshest data. Non-admins always get fast cached results. |
| **Three view modes** | See only **Tables** (direct lineage), only **Pipelines** (job dependencies), or **Full** (both). Switch with one click — no page reload. |
| **Depth control** | Set depth to 1, 2, or N hops to focus on immediate neighbors instead of the entire lineage chain. Set to 0 for the full graph. |
| **In-graph search** | Press Cmd+K to search tables/views within a complex lineage graph. Select a result to zoom and center on that node instantly. |
| **Drag, reset, explore** | Drag nodes to rearrange the graph. Lost in a large lineage? Click **Reset** and the entire layout re-renders from scratch. |
| **Serverless job cost on pipeline nodes** | Pipeline nodes show 30-day serverless compute cost in bold (list price from `system.billing`). A configurable **Discount %** input in the toolbar applies customer-specific pricing instantly — no API call, pure client-side math. Classic compute jobs show no cost (by design). |
| **Ops dashboard built in** | Admins get real-time P50/P95/P99 latency, memory usage, cache inventory with manual eviction, thread pool status, and upgrade advisories. Runs in a separate tab — zero impact on graph rendering. |
| **No user data access** | The app SPN reads only metadata (`BROWSE` + `information_schema`). Zero `SELECT` on user tables. Ever. |

---

## Use Cases

### Tracing Data Flow Across Schemas

Select any table to see its full upstream and downstream lineage path highlighted on the DAG. Entity nodes (jobs, pipelines) appear between tables showing *which ETL process* moves your data — not just that the data moved.

### Column-Level Impact Analysis

Before changing a column, click it to trace every downstream column that depends on it. Purple edges show the exact transitive path from `system.access.column_lineage` — zero false positives.

### Cross-Schema and Cross-Catalog Lineage

Your gold table reads from tables across 4 different schemas? The graph shows all source tables as cyan dashed-border nodes with their full column metadata — even though they live in different schemas or catalogs. Click a column on the target table and trace the purple edge back to its source column in another schema. All from `system.access` — zero assumptions.

### Integrating Lineage into Existing Dashboards

Your team already has a dashboard listing tables? Add `?table=catalog.schema.table` deep links. Users click a table on your Azure/AWS app and land directly on its lineage graph in a new tab. No embedding, no shared auth, no code changes to your app.

### Monitoring App Health at Scale

Admins open the ops dashboard (separate tab via burger menu) to see P99 latency, cache utilization, in-flight queries, and memory usage. Manually evict stale cache entries or spot upgrade advisories — all without impacting users browsing lineage.

### Serverless Pipeline Cost Visibility

Pipeline nodes show their 30-day serverless compute cost directly on the graph — no separate cost dashboard needed. Enter a customer-specific discount percentage in the toolbar and all costs update instantly. See at a glance which ETL jobs cost the most and where optimization effort should go.

### Identifying Pipeline Dependencies

Switch to **Pipelines** view to see job-to-job dependencies derived from shared tables. If Job A writes to a table that Job B reads, the dependency edge appears automatically from UC lineage data.

---

## What It Does

- **Visualizes** table-to-table and column-to-column lineage as an interactive DAG with upstream/downstream path highlighting
- **Discovers** all tables, views, jobs, notebooks, and DLT pipelines from Unity Catalog system tables — including cross-schema and cross-catalog references
- **Connects** them with lineage edges (`feedsInto`, `writesTo`) and entity nodes showing which pipeline moves the data
- **Integrates** with external dashboards via deep links — one URL parameter, any platform
- **Caches** aggressively — 250MB memory-bounded LRU cache with 8-hour TTL, single-flight request coalescing
- **Scales** to 4,000 concurrent users on a single Databricks App instance with 64-thread pool
- **Monitors** itself — admin dashboard with latency percentiles, cache inventory, thread pool status, and manual eviction

---

## Features

### Graph & Canvas

- **Three view modes:** Pipelines (entity dependencies), Tables (direct edges), Full (both)
- **Depth control:** Limit to N table hops (1-99). Entity nodes are transparent (don't count as hops)
- **Scoped highlighting:** Click a table for full transitive path (indigo). Click an entity for one-hop scope (orange)
- **Column lineage:** Click a column to trace its flow (purple edges). Client-side transitive traversal on real UC edges
- **Search:** Cmd+K to find tables/views within the rendered graph
- **Interactive:** Drag nodes, zoom/pan, hover tooltips, orphan detection (amber border)
- **Large graph optimization:** 50+ node graphs render instantly (no staggered animation). fitView retries at 100/500/1000/2000ms
- **Cross-schema nodes:** Tables from other schemas/catalogs render with cyan dashed border and `CROSS-SCHEMA` badge. Full column metadata fetched from their `information_schema` via batch queries — expandable and clickable for column lineage tracing

### Landing Page

- **Table browser:** All tables grouped by `catalog.schema` with expandable accordion sections
- **Stat cards:** Total tables, catalogs, schemas, and type count
- **Charts:** Donut chart for type distribution, bar chart for tables per catalog
- **Filter:** Search bar to filter tables across all catalogs
- **One-click navigation:** Click any table row to view its lineage

### Admin & Ops

- **Admin dashboard (separate page):** P50/P95/P99 latency, memory RSS, cache inventory with eviction, thread pool status, request rate, uptime
- **Auto-refresh:** 10-second polling in its own tab — zero impact on graph rendering
- **Upgrade advisories:** Alerts when memory, latency, or cache utilization exceed thresholds
- **Live mode:** Admins toggle between cached data (instant) and live system table queries
- **Serverless job cost:** Pipeline nodes show 30-day serverless cost in bold green badge. Cost = DBUs × list price per DBU (`pricing.effective_list.default` from `system.billing.list_prices`). Only serverless jobs shown (SKU filter: `LIKE '%SERVERLESS%'`). Classic compute jobs show no cost. Cost cached with lineage data — no extra warehouse queries on repeat visits.
- **Discount input:** Configurable discount % (0-99) in toolbar, visible when viewing lineage. Enter customer-specific discount and all pipeline costs update instantly. Formula: `displayed_cost = list_cost × (1 - discount/100)`. Hover tooltip shows both list price and discounted price. Pure client-side math — no API calls.
- **Serverless price cache:** List price per DBU cached globally with 24-hour TTL (separate from the 8-hour lineage cache). Pre-fetched at app startup in a background task so first lineage load isn't delayed. If `system.billing` access is not granted, cost gracefully not shown.

### Integration

- **Deep links:** `?table=catalog.schema.table` jumps directly to lineage graph
- **Admin URL:** `?admin=true` opens the standalone admin dashboard
- **Burger menu:** Hamburger icon in toolbar for navigation to admin dashboard (new tab) and table explorer
- **External app ready:** Add `<a href>` links from any existing dashboard — React, HTML, Jinja2, or server-side

### Performance

- **Request coalescing:** 4,000 users = 1 DBSQL query per cache key
- **Memory-bounded cache:** 250MB default, LRU eviction, pre-tracked entry sizes
- **64-thread pool:** Concurrent blocking SDK/SQL calls within a single process
- **Per-user rate limiting:** Token-hash keyed (not IP), LRU-bounded at 10K users
- **Single-process architecture:** Preserves shared cache, coalescing, rate limits, and metrics. Thread pool explicitly set to 64 via `set_default_executor()` (default is 8 on 4-core app) to handle ~20 concurrent SQL queries + user info lookups
- **User identity cache:** Token-hash keyed (SHA-256 first 16 chars), 5-minute TTL, LRU-bounded at 1,000 entries. Checked before any Databricks API call to the control plane
- **Startup/shutdown cache management:** All stale caches cleared on startup via FastAPI lifespan handler. Caches also cleared on SIGTERM for graceful shutdown
- **Metrics collection:** Middleware records latency for all `/api/` requests into a 1,000-entry rolling deque. Powers P50/P95/P99 in the admin dashboard
- **365-day time window:** Lineage queries filter to `event_time > current_date() - INTERVAL 365 DAYS` (set via `LINEAGE_WINDOW_DAYS`). This is the max staleness a producing pipeline can have before its lineage drops off the view — kept wide so infrequently-run pipelines (monthly/quarterly/backfills) still show lineage; pair with `event_date` partition pruning to keep a wide window cheap
- **50K row limit:** Column lineage queries capped at `LIMIT 50000` to prevent runaway scans
- **Adjacency maps:** Frontend pre-computes upstream/downstream maps for O(1) traversal on hover/select (not O(n^2))
- **Jittered backoff:** If the coalescing leader fails, waiting threads use random backoff (0-10s spread) to avoid stampeding the warehouse

---

## View Modes & Node Types

### View Modes

| Mode | What Renders | Column Toggle | Use Case |
|---|---|---|---|
| **Pipelines** | Entity nodes only, connected by pipeline dependencies | Disabled | See which jobs depend on which |
| **Tables** | Table nodes only, direct table-to-table edges | Enabled | Classic lineage view |
| **Full** | Both tables and entity nodes with routed edges | Enabled | Complete picture: data flow + which pipeline moves it |

### Depth Control

| Setting | Behavior |
|---|---|
| `0` or empty | Full lineage -- no depth limit |
| `1` | Immediate neighbors only (one table hop upstream and downstream) |
| `N` (max 99) | N table hops in each direction |

Entity nodes (jobs, pipelines) are transparent -- they do not count as a hop. A depth of 1 with an intervening pipeline node still reaches the next table.

### Node Types

| Node Type | Visual | Description |
|---|---|---|
| **Table** | Dark card, 280px wide | A table or view. Shows name, type badge, expandable columns. |
| **Entity** | Smaller card, 200px wide | A job, notebook, or DLT pipeline. Green (ran today) or amber (stale). Shows serverless cost in bold green badge when available. Hover tooltip shows job name, owner, last run time, freshness status, and cost breakdown (list price, discount). |
| **Cross-Schema** | Cyan dashed border, 280px wide | A table from a different schema/catalog referenced in lineage. Shows `CROSS-SCHEMA` badge. Full column metadata fetched from source schema's `information_schema` — expandable and clickable for column lineage. |
| **Sharing boundary** | Dashed teal pill | A Delta Sharing relationship — a `SHARE`, `RECIPIENT`, or `PROVIDER` — injected automatically when lineage touches shared data. Not a real lineage object; it marks where data crosses the share/metastore boundary. |

**Delta Sharing badges:** table nodes that participate in a share carry a small badge — teal **"shared-out"** (this table is published into a share) or violet **"shared-in"** (this table comes from a Delta Share foreign catalog). These appear automatically; there is no toggle.

### Table Node Status

| Status | Visual | Meaning |
|---|---|---|
| **Connected** | Default border | Has both upstream and downstream lineage |
| **Root** | Default border | Source table -- feeds downstream but nothing feeds it |
| **Leaf** | Default border | Sink table -- receives data but nothing reads from it |
| **Orphan** | Amber border | No lineage recorded in system tables |
| **Cross-Schema** | Cyan dashed border | Table from another schema/catalog referenced via lineage edges |

### Edge Colors

| Context | Color | Description |
|---|---|---|
| Table lineage (hover/select) | Indigo | Full transitive upstream/downstream path |
| Pipeline scope (hover/select) | Orange | One-hop: direct source and target tables |
| Column lineage (click column) | Purple | Transitive column flow from UC edges |
| Delta Sharing boundary | Dashed teal | Table → share → recipient (outbound) or provider → table (inbound). A *sharing relationship*, not observed transform lineage. |

---

## User Identity & Live Mode

| User Type | Live Toggle | Cache Behavior | Admin Dashboard |
|---|---|---|---|
| **Workspace admin** (in `admins` group) | Enabled -- can toggle freely | `live=true` bypasses cache | Full access |
| **Non-admin** | Visible but locked (greyed out, lock icon) | `live=true` silently downgraded to `live=false` | 403 Forbidden |

Both user types receive data on cold cache -- the normal cache-fill flow queries SQL and stores the result. Live mode only controls whether an admin can force a cache bypass.

To use a different admin group, set `ADMIN_GROUP_NAME`:

```yaml
env:
  - name: ADMIN_GROUP_NAME
    value: "lineage-admins"
```

---

## Admin Dashboard

The Admin Dashboard runs as a **separate page** to avoid competing with graph rendering on large graphs (200+ nodes).

**How to access:**

| Method | Description |
|---|---|
| Burger menu (top right) | Click the hamburger icon in the toolbar, then "Admin Dashboard" -- opens in a new tab |
| Direct URL | Navigate to `<app-url>/?admin=true` |
| Non-admins | See a 403 "Access Denied" page |

**Why a separate page (not an overlay):**

The admin dashboard auto-refreshes every 10 seconds, rendering 330+ cache inventory rows with metrics cards. When overlaid on a large lineage graph, this caused:
- Main thread contention (graph rendering + dashboard polling fighting for CPU)
- Browser freezes on graphs with 200+ nodes
- Rate limit exhaustion (admin polling eating into the per-user request budget)

Moving it to a separate tab eliminates all three issues.

**Metrics displayed:**

| Metric | Description |
|---|---|
| P50 / P95 / P99 latency | Request latency percentiles from the last 1,000 requests |
| Memory RSS | Process resident memory in MB and percentage of 6GB app runtime |
| Cache memory | Memory used vs. 250MB limit, entry count, and memory-based utilization percentage |
| Cache inventory | Top 15 entries by size with TTL remaining, expiry status, and per-entry evict buttons |
| Thread pool status | Max workers (64) and currently in-flight cache keys |
| Request rate | Total requests and requests per minute |
| Uptime | Process uptime, PID, and Python version |

---

## Lineage Data Source

All data comes exclusively from Unity Catalog system tables:

| System Table | What It Provides |
|---|---|
| `system.access.table_lineage` | Table-to-table data flow + entity metadata (job/pipeline/notebook, `event_time`, `created_by`) |
| `system.access.column_lineage` | Column-level data flow between tables (one query per schema, cached; transitive traversal on client) |
| `system.lakeflow.jobs` | Job names and owners for entity display names |
| `{catalog}.information_schema.tables` | Table metadata (names, types, owners) |
| `{catalog}.information_schema.columns` | Column metadata (names, types) |

**Query types that generate lineage:**

| Query Type | Example | What UC Captures |
|---|---|---|
| `CREATE TABLE AS SELECT` | `CREATE TABLE gold.summary AS SELECT ... FROM silver.orders` | `silver.orders` -> `gold.summary` (table + column level) |
| `INSERT INTO ... SELECT` | `INSERT INTO silver.cleaned SELECT ... FROM bronze.raw` | `bronze.raw` -> `silver.cleaned` |
| `MERGE INTO` | `MERGE INTO target USING source ON ...` | `source` -> `target` |
| `CREATE VIEW` | `CREATE VIEW vw AS SELECT ... FROM t1 JOIN t2` | `t1` -> `vw`, `t2` -> `vw` |

No inference, no regex, no heuristics. Zero false positives.

> **Note**: Some scenarios are not captured by UC (path-based access, RDD operations, certain DLT patterns). See [UC lineage docs](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage) and [system tables reference](https://docs.databricks.com/aws/en/admin/system-tables/lineage).

---

## Limitations & Scale

### Canvas Rendering

The frontend uses [React Flow](https://reactflow.dev/) with [ELK.js](https://www.eclipse.org/elk/) for graph layout.

| Graph Size | Expected Experience |
|---|---|
| < 50 nodes | Smooth — staggered reveal animation, instant interactions |
| 50-200 nodes | Good — instant reveal, minor layout delay |
| 200-500 nodes | Usable — ELK layout takes 3-10 seconds, interactions responsive after render |
| 500+ nodes | Use depth control to limit hops, or switch to Tables-only view |

### System Table Blind Spots

| Limitation | Affected Features |
|---|---|
| UC captures SQL operations only | Path-based access, RDD operations, some DLT patterns have no lineage |
| 365-day lineage window | Lineage whose producer last ran >365 days ago doesn't appear (configurable via `LINEAGE_WINDOW_DAYS`) |
| Column lineage requires grants | `system.access.column_lineage` needs `SELECT` access |
| Entity names from `system.lakeflow.jobs` | Only Lakeflow jobs have resolved names; notebooks/queries show raw IDs |
| Ad-hoc SQL has no entity info | CTAS/INSERT run directly on warehouse have `entity_type=NULL` — no pipeline node shown (correct behavior, no job was involved) |
| Serverless cost only | Classic compute (interactive/job clusters) cost not shown — only `%SERVERLESS%` SKU from `system.billing.usage` |
| Billing data requires grants | `system.billing` needs `SELECT` access. If not granted, cost gracefully not shown |
| Lineage propagation delay | UC system tables can take 5-30 minutes to reflect new lineage from recent queries |

### Architecture Boundaries

| Constraint | Reason |
|---|---|
| **Single-process only** | Multi-worker breaks shared cache, coalescing, rate limits, and metrics |
| **No iframe embedding** | Databricks Apps proxy blocks cross-origin framing |
| **In-memory cache only** | All cache (lineage, price, user info) lost on app restart. First user after restart hits full query latency. Persistent cache is a future enhancement |
| **Deep links require 3-part FQDN** | `catalog.schema.table` format required; partial names not supported |
| **Single workspace** | Deep links only work for tables in the deployed workspace |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + React Flow + ELK.js + Zustand + Framer Motion + Tailwind CSS |
| Backend | Python 3.11+ + FastAPI + Uvicorn (single process, 64-thread pool) |
| Data | Databricks SDK + DBSQL against UC system tables |
| Cache | In-memory LRU (250MB, 8h TTL, single-flight coalescing) |
| Deployment | Databricks Asset Bundles (DABs) → Databricks Apps |

---

## Permissions Reference

Before deploying, review the permissions the app needs. The app uses a **bare minimum permission model** — metadata access only, zero access to user data.

**Two SPNs may need permissions:**

| SPN | What It Is | When It Exists |
|---|---|---|
| **App SPN** | Auto-created by Databricks when the app is deployed | After `bundle deploy` |
| **Deploying SPN** | External SPN used for CI/CD automation | Only when deploying via SPN |

**Required Permissions Matrix:**

| Permission | App SPN | Deploying SPN | Who Can Grant |
|---|---|---|---|
| `USE CATALOG` + `BROWSE` on target catalog | Yes | Yes (if SPN) | Catalog owner / `MANAGE` |
| `USE SCHEMA` on target schema(s) | Yes | Yes (if SPN) | Schema owner / `MANAGE` |
| ~~`SELECT` on target catalog/schema~~ | **No** | **No** | -- |
| `USE CATALOG` on `system` | Yes | No | Account admin |
| `USE SCHEMA` + `SELECT` on `system.access` | Yes | No | Account admin |
| `USE SCHEMA` + `SELECT` on `system.lakeflow` | For entity names | No | Usually pre-granted via `account users` |
| `USE SCHEMA` + `SELECT` on `system.billing` | For serverless job costs | No | Account admin. If not granted, cost simply won't show (graceful fallback) |
| `USE SCHEMA` + `SELECT` on `system.information_schema` | For Delta Sharing overlay (shares/recipients/providers) | No | Account admin. Sharing views only expose objects the SP is privileged on — a full sharing inventory needs metastore-admin or explicit share grants |
| `CAN_USE` on SQL Warehouse | Yes | No | Warehouse owner / admin |
| `CAN_MANAGE` on SQL Warehouse | No | Yes (if SPN) | Warehouse owner / admin |
| Workspace membership | Auto (app SPN added) | Must exist in workspace | Workspace admin |

> **Data isolation:** The app SPN has zero access to user data. `BROWSE` grants visibility into object metadata (table names, column names, data types) via `information_schema`. The only `SELECT` is on `system.access` for lineage relationships.

> If `account users` already has grants on `system` (common in many workspaces), explicit per-SPN grants are unnecessary. Verify with `SHOW GRANTS ON CATALOG system`.

---

## Quick Start

### Prerequisites

| Requirement | How to Check |
|---|---|
| [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/) v0.239+ | `databricks --version` |
| CLI authenticated to target workspace | `databricks auth login --profile <name>` |
| SQL Warehouse (serverless or pro) | `databricks warehouses list` |
| Unity Catalog enabled | At least one catalog with data |
| **System tables enabled** — `system.access` (lineage) and `system.billing` (cost) | Account console → Settings → System tables. **Without `system.access` the app shows no lineage at all** — this is the most common "empty app" cause. |
| Node.js 18+ (only if rebuilding frontend) | `node --version` — pre-built `dist/` is committed |

### Deploy

```bash
# 1. Clone
git clone <repo-url> && cd lineage-explorer

# 2. Authenticate (human user — interactive OAuth)
databricks auth login --profile <your-profile>

# 3. Deploy
databricks bundle deploy -t dev \
  --profile <your-profile> \
  --var warehouse_id=<your-warehouse-id>

# 4. Start
databricks bundle run lineage-explorer -t dev --profile <your-profile>

# 5. Get URL
databricks apps get lineage-explorer-dev --profile <your-profile> -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['url'])"
```

**Deploying via Service Principal (CI/CD):**

Create a profile in `~/.databrickscfg`:

```ini
[my-spn-profile]
host          = https://my-workspace.cloud.databricks.com
client_id     = <spn-client-id>
client_secret = <spn-secret>
auth_type     = oauth-m2m
```

Then deploy with `--profile my-spn-profile`. See [OAuth M2M authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m).

> **Note:** The deploying SPN also needs `USE CATALOG` + `BROWSE` on target catalogs and `CAN_MANAGE` on the warehouse. `CAN_MANAGE` (not `CAN_USE`) is required because `bundle deploy` attaches the warehouse as an app resource. See permissions reference below.

### Post-Deploy Setup

**Enable user authorization (for live mode + admin dashboard):**

1. **Settings > Workspace > Previews** → Enable "Databricks Apps - On-Behalf-Of User Authorization"
2. **Compute > Apps > lineage-explorer-dev** → Edit → User Authorization → Add scopes: `iam.current-user:read`, `iam.access-control:read`

**Grant permissions to the app SPN:**

```bash
APP_SPN=$(databricks apps get lineage-explorer-dev --profile <your-profile> -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['service_principal_client_id'])")
```

A ready-to-run template lives in **[`setup.sql`](setup.sql)** — fill in the app SPN + catalog(s) and run it as a metastore admin. It covers:

```sql
-- Target catalog (catalog owner or MANAGE) — repeat per catalog you want explorable.
-- NOTE: end-to-end traces span catalogs, so grant on EVERY catalog in the lineage you
-- want visible (e.g. bronze in cat A, gold in cat B). A missing catalog = a gap in the trace.
GRANT USE CATALOG ON CATALOG <catalog> TO `<app-spn>`;
GRANT BROWSE ON CATALOG <catalog> TO `<app-spn>`;

-- System tables — lineage (account admin)
GRANT USE CATALOG ON CATALOG system TO `<app-spn>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<app-spn>`;

-- System tables — serverless cost (account admin, optional; graceful fallback if missing)
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing TO `<app-spn>`;

-- System tables — Delta Sharing overlay (account admin, optional)
GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO `<app-spn>`;
```

**Warehouse access** (via UI or API):

```bash
curl -X PATCH "https://<workspace-host>/api/2.0/permissions/sql/warehouses/<warehouse-id>" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"access_control_list": [{"service_principal_name": "<app-spn>", "permission_level": "CAN_USE"}]}'
```

Or: **SQL Warehouses > Your Warehouse > Permissions > Add the app SPN with "Can Use"**

> The app needs zero `SELECT` on user catalogs. It reads only metadata via `BROWSE`.

---

## Integration Guide

### Deep Link URLs

| URL | Behavior |
|---|---|
| `/` | Dashboard landing page with table browser |
| `/?table=catalog.schema.table` | Direct lineage graph for the specified table |
| `/?admin=true` | Admin dashboard (admin-only) |

### Integrating with an Existing App (Step-by-Step)

If you already have a web application (React, Flask, Azure App Service, or any platform) that displays Unity Catalog table names, follow these steps to add "View Lineage" links.

**Step 1: Get your Lineage Explorer URL**

```bash
databricks apps get <app-name> --profile <profile> -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['url'])"
```

**Step 2: Add links to your app**

React/JSX:

```jsx
const LINEAGE_URL = "https://lineage-explorer-dev-123456.aws.databricksapps.com";

function TableRow({ catalog, schema, table }) {
  const fqdn = `${catalog}.${schema}.${table}`;
  return (
    <tr>
      <td>{fqdn}</td>
      <td>
        <a href={`${LINEAGE_URL}/?table=${encodeURIComponent(fqdn)}`}
           target="_blank" rel="noopener noreferrer">
          View Lineage
        </a>
      </td>
    </tr>
  );
}
```

Plain HTML:

```html
<a href="https://<lineage-url>/?table=my_catalog.silver.orders"
   target="_blank" rel="noopener noreferrer">View Lineage</a>
```

Python (Jinja2):

```html
{% for t in tables %}
<a href="{{ lineage_url }}/?table={{ t.catalog }}.{{ t.schema }}.{{ t.name }}"
   target="_blank">View Lineage</a>
{% endfor %}
```

FastAPI (server-side links):

```python
LINEAGE_URL = os.environ["LINEAGE_EXPLORER_URL"]

@app.get("/api/tables")
def list_tables():
    return [
        {"fqdn": f"{t.catalog}.{t.schema}.{t.name}",
         "lineage_url": f"{LINEAGE_URL}/?table={t.catalog}.{t.schema}.{t.name}"}
        for t in get_tables()
    ]
```

**Step 3: Store the URL as config (recommended)**

```bash
# Azure App Service / deployment config
LINEAGE_EXPLORER_URL=https://lineage-explorer-dev-123456.aws.databricksapps.com
```

Switch between dev/prod deployments without code changes.

**Step 4: Verify**

1. Click "View Lineage" on your app
2. New tab opens → Databricks OAuth (if needed) → lineage graph loads automatically

### Authentication

Both apps use **independent auth**. No shared tokens or sessions.

| App | Auth | Handled By |
|---|---|---|
| Your app | Your auth (AAD, OAuth, SAML) | Your app |
| Lineage Explorer | Databricks OAuth via Apps proxy | Databricks |

### How It Works Internally

1. User clicks the deep link on the external app
2. Browser opens the Lineage Explorer URL with `?table=catalog.schema.table`
3. The Databricks Apps proxy intercepts the request and redirects to OAuth if the user is not authenticated
4. After authentication, the React app loads and reads the `?table=` parameter
5. The app fetches user info (admin status) first, then calls `/api/lineage?catalog=X&schema=Y` to load lineage data
6. The URL is cleaned to `/` via `history.replaceState()` -- no stale params on page refresh
7. The lineage graph renders with the specified table highlighted as the focus node

### Prerequisites

| Requirement | Notes |
|---|---|
| Lineage Explorer deployed as a Databricks App | Follow [Quick Start](#quick-start) |
| Your Lineage Explorer app URL | e.g., `https://lineage-explorer-dev-123456.aws.databricksapps.com` |
| Your existing app displays table names in `catalog.schema.table` format | The deep link requires all three parts |
| Users of your existing app have Databricks workspace access | They authenticate via Databricks OAuth when opening the lineage link |

### Limitations

| Limitation | Details |
|---|---|
| **Iframe embedding** | Not supported -- the Databricks Apps proxy sets `X-Frame-Options` headers that block cross-origin framing. Use `target="_blank"` links instead. |
| **Table FQDN format** | Must be exactly `catalog.schema.table` (three dot-separated parts). Partial names like `schema.table` are not supported. |
| **Cross-workspace** | Deep links only work for tables visible in the workspace where the Lineage Explorer is deployed. |

---

## Caching & Concurrency

### Cache Keys

| Cache | Key Pattern | Example | Shared? |
|---|---|---|---|
| Lineage graph (+ cost) | `lineage:{catalog}.{schema}` | `lineage:my_catalog.silver` | Yes -- all users share (8h TTL) |
| Columns | `columns:{catalog}.{schema}.{table}` | `columns:my_catalog.silver.orders` | Yes -- all users share (8h TTL) |
| Column lineage | `col_lineage:{catalog}.{schema}` | `col_lineage:my_catalog.silver` | Yes -- all users share (8h TTL) |
| Schemas | `schemas:{catalog}` | `schemas:my_catalog` | Yes -- all users share (8h TTL) |
| Catalogs | `catalogs` | `catalogs` | Yes -- all users share (8h TTL) |
| User info | token hash (SHA-256, 16 chars) | `a1b2c3d4e5f6...` | No -- per-user (5min TTL, 1K max LRU) |
| Serverless list price | global | `_serverless_price_per_dbu` | Yes -- all users share (24h TTL, pre-fetched at startup) |

**Multi-tier cache TTLs:**

| Cache Tier | TTL | Why |
|---|---|---|
| Lineage + cost data | 8 hours (configurable) | Lineage changes infrequently; 8h balances freshness vs warehouse cost |
| User identity (is_admin) | 5 minutes | Group membership can change; short TTL prevents stale admin status |
| Serverless list price per DBU | 24 hours | Price rarely changes; avoids slow `list_prices` query on every lineage load |

All caches are in-memory only. On app restart, everything starts cold. Serverless price is pre-fetched at startup in a background task.

### Memory Sizing Guide

The cache is bounded by **memory footprint** (default 250MB), not entry count. Size is computed once at insert time and tracked via a running total -- no re-serialization on reads, no lock contention in the admin dashboard.

| Workspace Scale | Tables | Estimated Cache | Fits in 250MB? |
|---|---|---|---|
| Small | ~100 | ~1-5MB | Yes (2%) |
| Medium | ~1,000 | ~11MB | Yes (4%) |
| Large | ~10,000 | ~104MB | Yes (42%) |
| Very large | ~20,000 | ~206MB | Yes (82%) |

Per-entry footprint (measured from real UC data):
- `all_tables` index: ~5.3KB per table (heaviest -- 106MB at 20K tables)
- `columns:*` per table: ~1.4KB
- `lineage:*` per schema: ~1.1KB per table in schema

The app runtime has 6GB RAM. At 250MB cache, only 4% is used for caching -- the rest is available for the Python process, request handling, and connection pools.

| `CACHE_MAX_MEMORY_MB` | Covers | % of 6GB Runtime |
|---|---|---|
| 50 | Small workspaces (~5K tables) | 0.8% |
| 250 (default) | Up to ~20K tables | 4% |
| 500 | Very large workspaces (40K+ tables) | 8% |

---

## Cost Optimization

Lineage Explorer uses a **Snapshot + Cache** pattern. User clicks are effectively free from a warehouse-cost perspective.

| Phase | What Happens | Cost Impact |
|---|---|---|
| **Cold cache miss** | One DBSQL query against `information_schema` + `system.access` | Single lightweight metadata query |
| **Warm cache hit** | Response from in-memory LRU cache (8h TTL) | $0 -- no warehouse query |
| **Concurrent requests** | Request coalescing: 4,000 users = 1 query | $0 additional |

The warehouse is never hit per-click or per-user. Cost is dominated by periodic cache refreshes.

**Minimizing cost:**

- Run the app only during business hours (e.g., 08:00-18:00 M-F) to save ~14 hours/day of compute
- Set the warehouse to **auto-stop after 1-5 min idle** — the cache means most requests never touch it, so a small serverless warehouse that idles to zero is ideal
- Use serverless budget policies to set spending caps on APPS and SQL SKUs
- The app SPN has metadata-only access -- all queries target `information_schema` and `system.access`, never user data

**The serverless-cost feature is the one heavy recurring query.** Pipeline/job cost comes from an account-wide aggregation of `system.billing.usage` (1-4 min on large accounts). It runs **only in the background**, never on the request path, and is cached for **6 hours** by default (`COST_CACHE_TTL_SECONDS` — billing rolls up at most daily, so refreshing more often just burns warehouse time). Two things to expect, by design:

- **Cost is blank for ~the first minute after a cold start** (the background scan hasn't finished) and fills in on the next load.
- **Brand-new pipelines/jobs show no cost for several hours** — that's `system.billing` ingestion lag, not a bug.

This stays 100% system-tables + in-memory: **no rollup table, no extra job, no extra storage**. (Per-graph cost queries are *not* used because `dlt_pipeline_id`/`job_id` are nested fields that can't prune the billing scan — filtering by ID is no faster than the global aggregation.)

**End-to-end trace cost:** selecting a table runs a directional BFS (several scoped `system.access` queries, ~30-60s the first time), then the whole result is cached (8h TTL) — re-selecting is instant and free. Very large lineages are capped at `LINEAGE_MAX_NODES` and the UI shows a "graph is partial" banner rather than silently truncating.

---

## Architecture

```
+---------------------------------------------------------------------+
|                         DATABRICKS APP                              |
|                                                                     |
|   +----------------------+          +--------------------------+    |
|   |    FastAPI Backend   |          |     React Frontend       |    |
|   |                      |  JSON    |                          |    |
|   |  Endpoints:          | ------>  |  React Flow  (DAG)       |    |
|   |    /api/tables       |          |  ELK.js     (layout)     |    |
|   |    /api/lineage      |          |  Framer     (animation)  |    |
|   |    /api/columns      |          |  Zustand    (state)      |    |
|   |    /api/entity-name  |          |  Tailwind   (dark UI)    |    |
|   |    /api/schema-column-lineage   |                          |    |
|   |    /api/admin/status |          |  Dashboard landing page  |    |
|   |    /api/user-info    |          |  Deep link routing       |    |
|   |    /health           |          |  Admin dashboard (page)  |    |
|   |                      |          +--------------------------+    |
|   |  Middleware:         |                                          |
|   |    64-thread pool                                               |
|   |    Per-user rate limiting                                       |
|   |    Request coalescing                                           |
|   |    Input validation  |                                          |
|   |    Error sanitization|                                          |
|   |    Metrics tracking  |                                          |
|   +----------+-----------+                                          |
|              |                                                      |
|              |  Memory-bounded LRU cache (250MB limit, 8h TTL)      |
|              |  Token-hash user cache (1000 entries, 5min TTL)      |
|              |  Request coalescing (single-flight per cache key)    |
|              |                                                      |
|              v                                                      |
|   +----------------------+                                          |
|   |   Databricks SDK     |  OAuth auto-injected                     |
|   +----------+-----------+                                          |
+--------------+------------------------------------------------------+
               |
               v
+---------------------------------------------------------------------+
|                       UNITY CATALOG                                 |
|   information_schema    system.access         system.lakeflow       |
|   +-- tables            +-- table_lineage     +-- jobs              |
|   +-- columns           +-- column_lineage                          |
+---------------------------------------------------------------------+
```

### Authentication Model

| Method | Env Vars Required | Use Case |
|---|---|---|
| **Databricks App (auto)** | None -- injected automatically | Production: running as a Databricks App |
| **Service Principal (OAuth M2M)** | `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` | CI/CD, automation |
| **Personal Access Token** | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | Local development only |

### Why Single-Process (Not Multi-Worker)

| Shared State | Breaks with Multi-Worker |
|---|---|
| In-memory cache | Each worker gets its own — same data fetched N times |
| Request coalescing | 4,000:1 drops to ~1,000:1 per worker |
| Rate limiting | Per-user limits multiply by worker count |
| Metrics | Admin dashboard shows partial data |

Instead: **64-thread pool** within one process. The app is I/O-bound (waiting on SQL), not CPU-bound.

### Scaling Beyond One Process

Single-process is the right default — it keeps the cache, coalescing, and rate limiter coherent without any external dependencies. When the workload outgrows one app instance:

| Bottleneck | Upgrade Path |
|---|---|
| RSS approaches the 6 GB app runtime ceiling | Raise `CACHE_MAX_MEMORY_MB` only if RSS has headroom; otherwise shorten `CACHE_TTL_SECONDS` so old entries expire faster |
| Sustained P99 latency above 5 s | Move the cache out of process — Lakebase Postgres or a Redis instance — so multiple Uvicorn workers can share state. Replace the `cachetools.TTLCache` with a thin client; keep request coalescing in-process per worker |
| `cache miss` rate spikes after deploys | Add a Databricks Job that warms the cache on a schedule (`/api/lineage` for the most-trafficked schemas) so first-user-after-restart isn't slow |
| 4 K concurrent users insufficient | Front the app with multiple instances behind a sticky load balancer. With Lakebase-backed cache, instances stay coherent without sticky sessions |

### Render Pipeline (275-node graph)

| Step | Time |
|---|---|
| Subgraph BFS (trace from focus table) | ~ms |
| ELK layout (layered, splines, crossing minimization) | 3-6s |
| React Flow mount (275 custom components) | 2-3s |
| Edge routing (spline paths) | 1-2s |
| fitView (retries at 100/500/1000/2000ms) | ~1s |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | (required) | SQL warehouse ID |
| `CACHE_TTL_SECONDS` | `28800` (8h) | Cache entry TTL |
| `CACHE_MAX_MEMORY_MB` | `250` | Max cache memory |
| `CACHE_MAX_ENTRIES` | `20000` | Max cache entries |
| `ADMIN_GROUP_NAME` | `admins` | Admin group for live mode + dashboard |
| `SQL_WAIT_TIMEOUT` | `50s` | SQL execution timeout |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Requests per user per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window |
| `LINEAGE_WINDOW_DAYS` | `365` | Lookback window for `system.access` lineage queries (max producer staleness before lineage drops off; UC retention ~365d caps it) |
| `COST_CACHE_TTL_SECONDS` | `21600` (6h) | TTL for the serverless-cost cache (billing rolls up daily) |
| `LINEAGE_MAX_NODES` | `2500` | Cap on nodes per graph/trace (UI flags partial results) |

Override via DABs:

```yaml
env:
  - name: DATABRICKS_WAREHOUSE_ID
    value: ${var.warehouse_id}
  - name: CACHE_TTL_SECONDS
    value: "3600"
  - name: RATE_LIMIT_MAX_REQUESTS
    value: "200"
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/user-info` | `{email, isAdmin}` |
| `GET` | `/api/admin/status` | Admin metrics (admin-only) |
| `GET` | `/api/tables` | All tables across catalogs |
| `GET` | `/api/catalogs` | Available catalogs |
| `GET` | `/api/schemas?catalog=X` | Schemas in catalog |
| `GET` | `/api/lineage?catalog=X&schema=Y&live=false` | Schema/catalog-scoped lineage graph. `live=true` bypasses cache (admin-only) |
| `GET` | `/api/lineage/trace?table=cat.schema.table` | **End-to-end cross-catalog trace** from a seed table (used on object selection). Returns `truncated:true` if capped |
| `GET` | `/api/sharing/overlay?catalog=X&schema=Y&audience=both` | Delta Sharing overlay (shared-in/out + provider/recipient boundary) for a scope |
| `GET` | `/api/sharing/overview` | Metastore-wide Delta Sharing inventory (shares, recipients, providers) |
| `GET` | `/api/columns?catalog=X&schema=Y&table=Z` | Table columns |
| `GET` | `/api/schema-column-lineage?catalog=X&schema=Y` | Column lineage edges (fetched per-schema; the client merges all schemas in a trace) |
| `GET` | `/api/column-lineage?catalog=X&schema=Y&table=Z&column=W` | Column-level lineage for a single column |
| `GET` | `/api/entity-name?entity_type=X&entity_id=Y` | Entity display name |
| `POST` | `/api/cache/invalidate` | Clear the full cache (**admin-only**) |
| `POST` | `/api/admin/evict-cache` | Evict a specific cache key (admin-only) |

All identifier parameters are validated: alphanumeric + underscores, max 255 chars.

---

## Development

### Local Setup

```bash
# Backend
pip install -r requirements.txt
export DATABRICKS_HOST="https://<workspace>.cloud.databricks.com"
export DATABRICKS_TOKEN="<your-pat>"
export DATABRICKS_WAREHOUSE_ID="<warehouse-id>"
uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

### Project Structure

```
lineage-explorer/
+-- databricks.yml              # DABs config -- single source of truth for deployment (includes sync.exclude for dev files)
+-- requirements.txt            # Python deps (pinned ranges)
+-- .gitignore
+-- backend/
|   +-- __init__.py
|   +-- main.py                 # FastAPI app, middleware, admin dashboard, rate limiting
|   +-- lineage_service.py      # DBSQL queries against system tables, LRU cache, coalescing
|   +-- models.py               # Pydantic models
+-- frontend/
    +-- package.json
    +-- vite.config.ts           # Dev server proxy + build config
    +-- tsconfig.json
    +-- tailwind.config.ts
    +-- index.html
    +-- dist/                    # Built frontend (committed for deployment)
    +-- src/
        +-- main.tsx             # Entry with ErrorBoundary
        +-- App.tsx              # Root: routing (landing page, lineage graph, admin dashboard, deep links)
        +-- api/client.ts        # Typed API client
        +-- store/lineageStore.ts
        +-- lib/elkLayout.ts     # ELK.js graph layout
        +-- components/
            +-- LandingPage.tsx  # Dashboard landing page (table list, stats, charts)
            +-- AdminDashboard.tsx # Standalone admin dashboard (separate page)
            +-- graph/           # LineageCanvas, TableNode, EntityNode, AnimatedEdge
            +-- layout/          # Toolbar (view-mode dropdown, Options popover, burger menu)
            +-- ui/              # Skeleton, SearchDialog, TableTooltip, ErrorBoundary
```

### Security

| Protection | Description |
|---|---|
| Input Validation | Strict regex on all SQL-interpolated parameters |
| Path Traversal | Static file paths verified within dist directory |
| Rate Limiting | Per-user (token-hash), bounded memory (10K LRU) |
| Error Sanitization | Internal details never exposed to clients |
| Cache Protection | Cache invalidation is **admin-only** (verified via on-behalf-of user identity, not IP — the Apps proxy makes IP gating unreliable) |
| Error Boundary | React catches render errors with recovery UI |
| Startup Cache Clear | Stale caches cleared on every startup |
| Graceful Shutdown | Caches cleared on SIGTERM via FastAPI lifespan handler |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "No SQL warehouse available" | `DATABRICKS_WAREHOUSE_ID` not set | Pass `--var warehouse_id=<id>` during deploy |
| Empty lineage graph | App SPN lacks `SELECT` on `system.access` | Account admin must grant access |
| Tables visible but no edges | No lineage captured yet | Run a query (CTAS, INSERT, MERGE) |
| Catalog not visible | App SPN lacks `USE CATALOG` | Grant `USE CATALOG` + `BROWSE` |
| `bundle deploy` host mismatch | Wrong profile | Use `--profile <name>` matching your `~/.databrickscfg` |
| `bundle deploy` missing variable | No `--var warehouse_id` | Add `--var warehouse_id=<id>` to the deploy command |
| 429 Too Many Requests | Rate limit exceeded | Increase `RATE_LIMIT_MAX_REQUESTS` env var. Admin dashboard auto-refreshes every 10s -- set to `200` for dev environments. |
| 400 "Invalid catalog" | Special chars in identifier | Use only alphanumeric + underscores |
| Live toggle disabled for all | Workspace preview not enabled | Enable "Databricks Apps - On-Behalf-Of User Authorization Public Preview" in Settings > Workspace > Previews |
| Live toggle disabled for admin | OAuth scopes missing | Add `iam.current-user:read` and `iam.access-control:read` scopes via Databricks Apps UI |
| Live toggle disabled for admin | Admin group name mismatch | Default group is `admins`. Set `ADMIN_GROUP_NAME` env var if different. |
| Admin dashboard 403 | Not in admin group | Only workspace admins (members of the configured admin group) can access `/api/admin/status` |
| Deep link admin icon missing | Race condition on first load | Admin icon appears after `getUserInfo` API resolves. Deep links wait for user info before navigating, but if API is slow there may be a brief delay |
| Deep link empty graph | Viewport not centered | Click Reset or fit-to-view. fitView retries automatically |
| Browser freezes on large graph | 200+ nodes | Admin dashboard is now separate. Use depth control to limit scope |
| Graph takes 10+ seconds | ELK layout on 200+ nodes | Expected. Subsequent loads instant from cache |
| No cost on pipeline nodes | `system.billing` not granted, or list_prices query timed out | Grant `SELECT` on `system.billing`. Cost appears after price is cached (first load may need warm warehouse) |
| No pipeline node between sources and target | Ad-hoc SQL query (not a job) | Only Job/Notebook/Pipeline entities create pipeline nodes. Run the query via a Databricks Job to generate `entity_type=JOB` lineage |
| Cross-schema nodes have no columns | Column metadata fetch failed for external schema | Check that app SPN has `BROWSE` + `USE SCHEMA` on the source schema |
| Cost shows $0.00 | Job ran on classic compute | Cost only shows for serverless SKU. Classic compute cost is not supported |
| First load after restart is slow | All caches are in-memory, lost on restart | Expected. Serverless price pre-fetches at startup; lineage populates on first user request |

---

## License

MIT
