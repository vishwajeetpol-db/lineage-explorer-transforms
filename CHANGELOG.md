# Changelog

All notable changes to NEXUS Lineage are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
