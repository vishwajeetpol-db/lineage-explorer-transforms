# BrickTrace — Capability Matrix

> A single-page view of everything BrickTrace does. Version **2.5.5**.
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
| 19 | Observability | 🟡 | `/api/observability` (run health; no dedicated dashboard) |
| 20 | Notifications | ✅ | detection scan + alert rules + webhooks + delivery queue |

**Scorecard: 19 ✅ · 1 🟡 · 0 ⬜**

## 3. Table Lineage workspace (v2.5.5)

Per-table analysis workspace (`?view=tableLineage`) — catalog tree + lineage graph + draggable capability panels:

| Panel | Status | What it shows |
|---|---|---|
| Impact | ✅ | Downstream tables + consumer entities (dashboards/jobs/pipelines) grouped by type, clickable deep-links |
| Root Cause | ✅ | Health-based upstream trace: prime suspect + failure path from producer run health |
| Governance | ✅ | Owner/type/tags/classified columns + add/remove classification rules (admin) |
| Access & security | ✅ | Grantees, accessors, reads/writes, identities, recent events |
| ML Models | ✅ | Models trained on the table (UC Registry + MLflow) + serving status |
| LLM Transform | ✅ | AI-inferred column transforms · model dropdown · versioning · version compare |

## 4. Platform & UX

| Capability | Status | Notes |
|---|---|---|
| Light / dark theme | ✅ | Toggle (top-right), CSS-variable tokens, persisted (v2.5.5) |
| Sidebar-shell home | ✅ | Nav, workspace selector, user profile, Recent Activity (v2.5.5) |
| Deep-link routing | ✅ | `?table=`, `?view=`, `?admin=`, `?controlPanel=`, `?view=tableLineage` |
| Three view modes + depth | ✅ | Tables / Pipelines / Full + hop slider |
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
- **Observability (#19)** is API-only; no dedicated monitoring dashboard in-app yet.

_Last updated: v2.5.5. Source of truth for code anchors: [capability_code_map.md](capability_code_map.md)._
