# BrickTrace — Capability Matrix

> A single-page view of everything BrickTrace does. Version **2.6.0**.
> Status legend: ✅ **HAVE** (backend + shipped UI) · 🟡 **PARTIAL** (backend, limited UI/scope) · ⬜ **GAP**.
> For code anchors and debug notes, see [capability_code_map.md](capability_code_map.md).

---

## 1. Core lineage

| Capability | Status | What it does | Surface |
|---|---|---|---|
| Table & column lineage | ✅ | Cross-catalog trace from `system.access.table_lineage` / `column_lineage` — every source back to every target | Graph, `/api/lineage`, `/api/sublineage` |
| Expression-level transformation | ✅ | Reconstructs the actual SQL/PySpark expression that produced each column (parse-based) | Transform panel, `/api/transform` |
| Runtime plan capture | ✅ | Captures the exact Spark analyzed plan from a pipeline write (`df.explain`), used over static parse | `plan_capture/`, gated flag |
| Cross-schema / catalog enrichment | ✅ | External-in-scope tables render as full, expandable nodes | included in `/api/lineage` |
| Delta Sharing overlay | ✅ | Shared-in / shared-out boundary nodes | `get_sharing_overlay()` |
| Serverless cost on nodes | ✅ | 30-day list price per job/pipeline from `system.billing` | `/api/cost/{entity}` |

## 2. The 20-item scorecard

| # | Capability | Status | Route(s) |
|---|-----------|--------|----------|
| 01 | Automatic Discovery | ✅ | dbt/Airflow import + foreign-catalog crawl + manual register |
| 02 | End-to-End Lineage | ✅ | `/api/lineage/bi-consumers`, `/api/lineage/streaming-topology` |
| 03 | Column-Level Lineage | ✅ | `/api/sublineage` |
| 04 | Transformation Logic | ✅ | `/api/transform`, `/api/analyze-producer` |
| 05 | Multi-Platform | ✅ | OpenLineage producer bridge (`/api/external/ol-bridge/*`) |
| 06 | Near Real-Time | ✅ | TTL cache (8h) across `/api/lineage/*` |
| 07 | Versioned Lineage | ✅ | `/api/snapshots/capture\|diff\|auto-capture\|timeline` |
| 08 | Impact Analysis | ✅ | `/api/impact` — downstream + **consumers (readers) w/ deep links** |
| 09 | Root Cause Analysis | ✅ | `/api/root-cause/analyze` (column+DQ) · `/api/root-cause/trace` (health-based) |
| 10 | Business Lineage | ✅ | `/api/glossary/*` (terms, domains, KPIs, propagation) |
| 11 | Data Quality | ✅ | `/api/dq-rules/*` (rules, metrics, trends, pipeline expectations) |
| 12 | Governance | ✅ | `/api/governance` + `/api/governance/config` (classification rules) |
| 13 | Security & Access | ✅ | `/api/access` (grants, accessors, reads/writes, recent events) |
| 14 | AI/ML Lineage | ✅ | `/api/ml/*` (endpoints, models-for-table, feature/vector/prompt) |
| 15 | Interactive Viz | ✅ | React + ReactFlow + ELK.js graph |
| 16 | Search & Discovery | ✅ | `/api/search`, `/api/discover/*` |
| 17 | Open Standards | ✅ | OpenLineage export + import + live producer |
| 18 | Scalability | ✅ | Delta-backed distributed cache + cursor-paginated graph |
| 19 | Observability | ✅ | `/api/observability` (aggregate health) · **`/api/observability/runs`** (last-N runs w/ per-run cost + health verdict, on every job/pipeline node) |
| 20 | Notifications | ✅ | **background auto-scan** (periodic, off the request path) + alert rules + webhooks + delivery queue; scan dedups by natural key, prunes to a retention cap, and its detectors (schema-change, sensitive-flow, DQ) exclude platform/app-owned schemas and skip UC-classified targets · `/api/notifications/*` |

**Scorecard: 20 ✅ · 0 🟡 · 0 ⬜**

## 3. Table Lineage workspace (v2.5.5, extended v2.6.0)

Per-table analysis workspace (`?view=tableLineage`) — catalog tree + lineage graph + draggable capability panels. The Impact/Root Cause/Governance/Access panels are **cached per table** (v2.6.0) with a "cached / may be stale" badge + refresh icon; admins can evict from the ops dashboard.

| Panel | Status | What it shows |
|---|---|---|
| Impact | ✅ | Downstream tables + consumer entities (dashboards/jobs/pipelines) grouped by type, clickable deep-links · **cached** |
| Root Cause | ✅ | Health-based upstream trace: prime suspect + failure path from producer run health · **cached** |
| Governance | ✅ | Owner/type/tags/classified columns + add/remove classification rules (admin) · **cached** |
| Access & security | ✅ | Grantees, accessors, reads/writes, identities, recent events · **cached** |
| ML Models | ✅ | Models trained on the table (UC Registry + MLflow) + serving status |
| Column Transformation | ✅ | Per-column derivation (captured plan → CDC → stored/fresh LLM) · model dropdown · versioning · cross-source compare · **multi-producer side-by-side comparison (v2.6.0)** · actionable access-denied guidance |

## 3b. Run health & cache (v2.6.0)

| Capability | Status | Surface |
|---|---|---|
| Per-node run health check | ✅ | Activity icon on every JOB/PIPELINE node → verdict + success rate + duration trend + cost total/spike + last-5 runs (per-run cost + deep links) · `/api/observability/runs` |
| Per-table capability cache | ✅ | `capability_cache` Delta table; refresh badge per panel; `/api/admin/capability-cache` inventory + evict (entry/table/all) |
| Multi-producer transformation comparison | ✅ | Side-by-side per-column matrix when a table has 2+ producers · `/api/column-transformations/compare-producers` |

## 3c. Business view & AI graph explanation (v2.6.x)

A plain-language lens over the technical lineage graph for non-engineers — a **Technical ⇄ Business** toggle on the canvas (top-left). Client-side and instant; the preference persists.

| Capability | Status | What it does |
|---|---|---|
| Business relabelling | ✅ | Technical types → business terms (JOB→Process, PIPELINE→Data pipeline, NOTEBOOK→Code step, VIEW→View, MATERIALIZED_VIEW/MANAGED/EXTERNAL→Dataset, STREAMING_TABLE→Live dataset, VOLUME/PATH→File); `snake_case`→Title Case names; per-dataset plain-English description; hides FQNs, column detail, IDs, cost, health · `frontend/src/lib/businessView.ts` |
| Data-only vs Data + processing | ✅ | Sub-toggle to show only datasets (and how they connect) or datasets + the jobs/pipelines that move data between them · `businessDetail` in `lineageStore` |
| Precise dataset lineage (`table_edges`) | ✅ | Data-only uses the real per-row `(source→target)` pairs from `system.access.table_lineage` instead of cross-producting an entity's inputs × outputs — fixes the "everything connected to everything" mesh on hub tables · `LineageResponse.table_edges`, `GET /api/lineage/trace` |
| AI "Explain this lineage" | ✅ | Lightbulb (business view) → modal with a plain-English summary + ordered source→process→output walkthrough of the current on-screen graph · `POST /api/lineage/explain` (`llm.explain_lineage_graph`) |

## 4. Platform & UX

| Capability | Status | Notes |
|---|---|---|
| Light / dark theme | ✅ | Toggle (top-right), CSS-variable tokens, persisted (v2.5.5) |
| Sidebar-shell home | ✅ | Nav, workspace selector, user profile, Recent Activity (v2.5.5) |
| Deep-link routing | ✅ | `?table=`, `?view=`, `?admin=`, `?controlPanel=`, `?view=tableLineage` |
| Three view modes + depth | ✅ | Tables / Pipelines / Full + hop slider |
| Technical / Business view | ✅ | Canvas toggle flips the graph into a plain-language lens (relabel, hide detail, data-only vs data+processing, AI explain) · persisted (v2.6.x) |
| In-graph tools | ✅ | ⌘K search, drag, fit-view, orphan highlight |
| Excel export + preview | ✅ | Styled multi-sheet `.xlsx` |
| Admin ops dashboard | ✅ | Latency/memory/cache/thread-pool metrics (admin-gated) |
| Control Panel (feature flags) | ✅ | Admin toggle center for opt-in capabilities |
| Live mode | ✅ | Admin-only cache bypass, direct system-table reads |

## 5. Diagnostics & discovery

| Capability | Status | Route |
|---|---|---|
| SCD/CDC spec viewer | ✅ | `/api/diagnostics/scd` |
| Schema-change detector | ✅ | `/api/diagnostics/schema-changes` |
| Column profiling overlay | ✅ | `/api/diagnostics/profile` |
| PII / sensitive-column finder | ✅ | `/api/discover/sensitive` |
| Orphan-table detector | ✅ | `/api/discover/orphans` |
| Sensitivity propagation | ✅ | included in `/api/governance` |
| Pipeline capture installer | ✅ | `/api/pipeline/install-capture` |

---

## Access model & known limits

- **Metadata-only** — reads UC system tables + `BROWSE`; never reads table row data. App writes only to one app-owned schema.
- **Service-principal scoped** — some panels only show what the app SP can see: Access grants need `MANAGE` on the catalog/schema; audit-backed sections need account-admin `SELECT` on `system.access`.
- **ML model→table** derivation needs the training run readable by the SP (fails if the run's notebook was deleted).
- **Column Transformation & run health** need the app SP to read the producer's source/runs: jobs need `CAN_VIEW` **and** their task notebook needs `CAN_READ`; pipeline `glob`/`file` source needs read on the workspace files. The panel surfaces the exact grant when access is denied.
- **Per-run cost** in the run-health check joins `system.billing.usage` on `job_run_id` / `dlt_update_id`; without `SELECT` on `system.billing` the runs still show but costs are blank.

_Last updated: v2.6.0. Source of truth for code anchors: [capability_code_map.md](capability_code_map.md)._
