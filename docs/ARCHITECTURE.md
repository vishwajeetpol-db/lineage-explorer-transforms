# NEXUS Lineage — Architecture & Functionality Reference

> **Version**: 2.0.0  
> **Last Updated**: 2025-06-22  
> **Tech Stack**: FastAPI · React · TypeScript · ReactFlow · ELK.js · Databricks Apps · DABs

---

## 1. Overview

NEXUS Lineage is a unified, self-contained Databricks App that provides:

1. **Table-level lineage** — end-to-end DAG visualization across all Unity Catalog catalogs
2. **Column-level lineage** — traced from `system.access.column_lineage` edges
3. **Transformation lineage** — expression-aware column derivation (ARITHMETIC, WINDOW, AGGREGATE, CAST, etc.) with BFS backtracking
4. **Lineage Builder** — serverless job orchestration for building transformation lineage on-demand
5. **Delta Sharing** — provider/recipient boundary visualization
6. **Serverless cost** — per-entity 30-day billing from `system.billing`
7. **Admin dashboard** — real-time P50/P95/P99 latency, memory, cache inventory
8. **Excel export** — styled multi-sheet workbook of lineage data

All functionality is **self-contained** — zero external code references. The app deploys as a single unit via Declarative Automation Bundles.

---

## 2. Directory Structure

```
combined_lineage_App/
├── backend/                         # FastAPI application
│   ├── main.py                      # App entry, routes, middleware, rate limiting
│   ├── lineage_service.py           # Table/column lineage (system.access queries)
│   ├── transform_service.py         # Transformation lineage (BFS backtracking)
│   ├── build_service.py             # Lineage Builder (job submission & polling)
│   ├── excel_export.py              # Styled .xlsx export generation
│   ├── models.py                    # Pydantic models for all API responses
│   ├── parallel.py                  # Parallelization utilities
│   ├── perf_patches.py              # Runtime performance patches
│   ├── startup.py                   # Startup hook for patches
│   ├── transformation_lineage/      # Backend-embedded TL library
│   │   ├── pipeline.py              # Inline pipeline orchestration
│   │   ├── config.py                # Configuration dataclass
│   │   ├── types.py                 # Type definitions
│   │   └── parsing/                 # SQL/PySpark parsers
│   └── tests/                       # Unit tests
├── frontend/                        # React + TypeScript SPA
│   ├── src/
│   │   ├── App.tsx                  # Route-based rendering
│   │   ├── components/
│   │   │   ├── graph/               # ReactFlow canvas, ELK layout
│   │   │   ├── transform/           # TransformPanel, TransformCanvas, BuildProgress
│   │   │   ├── lineage/             # LineagePreview (column drill-down)
│   │   │   ├── browse/              # Catalog/Schema/Table list views
│   │   │   ├── landing/             # Landing page, GlobalSearch
│   │   │   ├── layout/              # Toolbar, navigation
│   │   │   └── ui/                  # Shared UI primitives
│   │   ├── api/                     # API client (typed fetch wrappers)
│   │   ├── store/                   # Zustand state management
│   │   ├── hooks/                   # Custom React hooks
│   │   └── lib/                     # Utilities, ELK worker
│   └── dist/                        # Production build (served by FastAPI)
├── transformation_lineage/          # Full pipeline library (for notebook execution)
│   ├── pipeline.py                  # 8-phase orchestrator
│   ├── config.py                    # LineageJobConfig
│   ├── types.py                     # Shared types
│   ├── extraction/                  # Artifact discovery & fetching
│   ├── parsing/                     # SQL parser, graph builder
│   ├── versioning/                  # Content SHA change detection
│   ├── reconciliation/              # System lineage reconciliation
│   ├── materialization/             # KPI subgraph caching
│   ├── sublineage/                  # Edge endpoints builder
│   └── storage/                     # Schema creation, Delta writers
├── notebooks/
│   └── run_pipeline                 # Databricks notebook executed by build jobs
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

- **Auto-derives** the pipeline notebook path from deployment location
- **REST API submission** (`/api/2.1/jobs/runs/submit`) for one-time serverless jobs
- **Environment spec** with pinned dependencies (`sqlparse`, `requests`, `databricks-sdk`)
- **Progress polling** via SDK `get_run()` with lifecycle-to-step mapping
- **8 build steps**: Validating Table → Initializing Job → Schema Discovery → SQL Extraction → Dependency Parsing → Graph Construction → Edge Materialization → Cache Update

### 4.2 Transform Service (`transform_service.py`)

- **Freshness check** — single-query COUNT+MAX (eliminates double roundtrip)
- **BFS backtracking** — walks upstream edges from target column to source columns
- **Category resolution** — maps transforms to 13 categories with colors
- **Single-flight coalescing** — bounded per-key lock pool prevents thundering herd
- **Memory-bounded TTL cache** — 64MB max, configurable TTL
- **Parallel SQL** — 4-thread pool for concurrent edge/category queries

### 4.3 Pipeline (`transformation_lineage/pipeline.py`)

The 8-phase transformation lineage pipeline:

1. **Extraction** — discover notebooks/files via UC lineage, fetch artifact source code
2. **Version Check** — batch SHA256 comparison, skip unchanged artifacts
3. **Parse + Graph** — parallel (8 threads) SQL/PySpark parsing + graph construction
4. **Write Results** — deduped nodes + edges to Delta tables
5. **Reconciliation** — stats against system lineage
6. **Materialization** — KPI subgraph cache for fast serving
7. **Edge Endpoints** — build denormalized endpoint table for app queries
8. **Expression Enrichment** — LLM-based PySpark→SQL translation (best-effort)

### 4.4 Frontend Components

| Component | File | Function |
|-----------|------|----------|
| TransformPanel | `TransformPanel.tsx` | Slide-out panel triggered from column click |
| TransformCanvas | `TransformCanvas.tsx` | ReactFlow canvas for transformation DAG |
| TransformNode | `TransformNode.tsx` | Column nodes with table/depth coloring |
| TransformEdge | `TransformEdge.tsx` | Animated edges with category colors |
| BuildProgress | `BuildProgress.tsx` | Real-time job progress with step DAG |
| FreshnessBadge | `FreshnessBadge.tsx` | Staleness indicator with auto-build prompt |
| PruningControls | `PruningControls.tsx` | Depth/category filtering for complex graphs |

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
| POST | `/api/transform/build` | Submit build job |
| GET | `/api/transform/status/{run_id}` | Poll build progress |
| GET | `/api/transform/trace?...` | BFS backtrack column |
| GET | `/api/transform/categories` | Category→color mapping |
| GET | `/api/transform/build-configured` | Check if pipeline is configured |

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
| Early termination | `pipeline.py` | Skip parse/graph if no new artifacts |
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
| Empty lineage (no edges) | Returns `has_lineage: false`, UI shows "No lineage" state |
| Stale transformation lineage | FreshnessBadge prompts rebuild with one click |
| Column is a source (no upstream) | `is_source_column: true` flag, distinct UI state |
| Max depth reached in BFS | Stops at configured limit, reports `max_depth_reached` |
| Concurrent build requests | Freshness check prevents redundant jobs |
| Build job failure | `BuildJobStatus.is_success=false` with state_message |
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
GRANT USE SCHEMA ON SCHEMA system.access TO `<app-spn>`;
GRANT SELECT ON TABLE system.access.table_lineage TO `<app-spn>`;
GRANT SELECT ON TABLE system.access.column_lineage TO `<app-spn>`;
GRANT USE CATALOG ON CATALOG system TO `<app-spn>`;
```

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `DATABRICKS_WAREHOUSE_ID` | — | SQL Warehouse ID (required) |
| `LINEAGE_CATALOG` | `lattice_lineage` | Catalog for transform lineage tables |
| `LINEAGE_SCHEMA` | `lineage` | Schema for transform lineage tables |
| `PIPELINE_NOTEBOOK_PATH` | (auto-derived) | Path to `run_pipeline` notebook |
| `CACHE_TTL_SECONDS` | `28800` | Main lineage cache TTL |
| `CACHE_MAX_MEMORY_MB` | `250` | Max memory for lineage cache |
| `TRANSFORM_CACHE_TTL_SECONDS` | `3600` | Transform cache TTL |
| `BUILD_CACHE_TTL_HOURS` | `24` | Hours before lineage is stale |
| `ADMIN_GROUP_NAME` | `admins` | Group for admin access |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Max requests/user/window |

---

## 9. Security

- **Input validation**: All identifiers validated against `^[A-Za-z0-9_]{1,255}
- **CSP headers**: `frame-ancestors 'none'`, strict `script-src`
- **Path traversal protection**: `os.path.realpath` + prefix check
- **Rate limiting**: Per-user (token-hashed), LRU-bounded at 10K users
- **Admin gating**: Group membership check via user's own OAuth token
- **Error sanitization**: Internal paths/SQL never exposed in API responses
- **No row data access**: App reads only metadata + system tables
