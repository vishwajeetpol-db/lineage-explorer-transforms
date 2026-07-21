# Capability → Code Map

> Debugging index for BrickTrace. Maps the **20 scorecard capabilities** (from capability-matrix PDF)
> AND the **full feature inventory** (48 routes/subsystems) to code anchors.
> Status: **HAVE** / **PARTIAL** / **GAP** — with closure notes for each.

---

## Part A — Scorecard Capabilities (20)

| # | Capability | Status | Primary Files | Route(s) | Notes |
|---|-----------|--------|---------------|----------|-------|
| 01 | Automatic Discovery | HAVE | `routes/external_sources.py`, `federated_service.py` | `POST /api/external/dbt/import`, `/api/external/airflow/import`, `/api/external/discover`, `GET /api/lineage/federated-overlay` | dbt manifest + Airflow DAG import + manual registration + foreign catalog crawl |
| 02 | End-to-End Lineage | HAVE | `routes/external_sources.py`, `routes/capability_closures.py` | `/api/lineage/bi-consumers`, `/api/lineage/streaming-topology` | BI tool detection via query history user-agents + streaming table topology + dbt/Airflow + federated + Delta Sharing |
| 03 | Column-Level Lineage | HAVE | `sublineage/backtrack.py`, `transform_service.py` | `GET /api/sublineage` | — |
| 04 | Transformation Logic | HAVE | `transform_service.py`, `build_service.py`, `plan_capture/` | `GET /api/transform`, `POST /api/analyze-producer` | — |
| 05 | Multi-Platform | HAVE | `routes/external_sources.py` (ol-bridge section), `federated_service.py` | `POST /api/external/ol-bridge/register`, `POST /api/external/ol-bridge/ingest/{source_id}`, `GET /api/external/ol-bridge/sources`, `GET /api/external/ol-bridge/events`, `GET /api/lineage/federated-overlay` | OL producer bridge: external OL-emitting platforms (Snowflake Horizon, BigQuery via OL proxy, Spark + openlineage-spark, Flink, dbt Cloud, Airflow 2.7+) register once and push standard RunEvents to a dedicated receive URL; edges stored in `external_ol_bridge_events` and surfaced in the lineage graph |
| 06 | Near Real-Time | HAVE | `lineage_service.py` (TTL cache 8h) | all `/api/lineage/*` | — |
| 07 | Versioned Lineage | HAVE | `routes/graph_snapshots.py`, `routes/capability_closures.py` | `POST /api/snapshots/capture`, `GET /api/snapshots/diff`, `POST /api/snapshots/auto-capture`, `GET /api/snapshots/timeline` | On-demand + scheduled capture, diff viewer, growth timeline |
| 08 | Impact Analysis | HAVE | `routes/impact.py` | `GET /api/impact` | — |
| 09 | Root Cause Analysis | HAVE | `server/root_cause.py`, `routes/root_cause.py` | `POST /api/root-cause/analyze` | BFS walk + failed runs + DQ rule violation scoring |
| 10 | Business Lineage | HAVE| `routes/glossary.py` | `/api/glossary/terms`, `/api/glossary/domains`, `/api/glossary/kpis`, `/api/glossary/link`, `/api/glossary/propagate-suggestions`, `/api/glossary/lineage-overlay` | Full glossary: term CRUD + domains + KPIs + term→table/column linking + downstream propagation suggestions + lineage graph overlay |
| 11 | Data Quality | HAVE | `routes/dq.py`, `routes/capability_closures.py` | `/api/dq-rules/metrics`, `/api/dq-rules/trends`, `/api/dq-rules/pipeline-expectations`, `/api/dq-rules/propagation` | Live metrics + trend history + pipeline expectation sync + upstream propagation |
| 12 | Governance | HAVE | `routes/governance.py` | `GET /api/governance` | — |
| 13 | Security & Access | HAVE | `routes/access.py` | `GET /api/access` | — |
| 14 | AI/ML Lineage | HAVE | `routes/ml.py` | `/api/ml/endpoints`, `/api/ml/feature-tables`, `/api/ml/vector-indexes`, `/api/ml/vector-lineage`, `/api/ml/prompt-lineage` | Serving endpoints + feature store + vector search + prompt/inference chain + model registry |
| 15 | Interactive Viz | HAVE | `frontend/dist/` (React+ELK.js) | static serving | — |
| 16 | Search & Discovery | HAVE | `routes/search.py`, `routes/discovery.py` | `/api/search`, `/api/discover/*` | — |
| 17 | Open Standards | HAVE| `routes/openlineage.py` | `GET /api/export/openlineage`, `POST /api/import/openlineage`, `POST /api/openlineage/producer/produce`, `GET /api/openlineage/producer/events` | Full bidirectional: export + ingest + live producer (detects writes → queues OL events → configured endpoints deliver) |
| 18 | Scalability | HAVE | `routes/scalability.py`, `cache_service.py`, `perf_patches.py`, `main.py` | `GET /api/scalability/graph`, `GET /api/scalability/cache/stats`, `POST /api/scalability/cache/invalidate`, `GET /api/scalability/health` | Delta-backed distributed cache (shared across replicas, no Redis needed) + BFS cursor-paginated graph (removes 400-node cap) + parallel catalog enumeration + 8-worker pool |
| 19 | Observability | HAVE | `routes/observability.py` | `GET /api/observability` | — |
| 20 | Notifications | HAVE | `routes/notifications.py`, `routes/capability_closures.py` | `/api/notifications/scan`, `/api/notifications/webhooks`, `/api/notifications/enqueue-delivery` | Detection (schema/DQ/sensitive) + alert rules + webhook registration + delivery queue |

**Summary**: 20 HAVE, 0 PARTIAL, 0 GAP — full scorecard coverage achieved (v2.5.4)

---

## Part B — Full Feature Inventory (beyond scorecard)

| # | Feature | Primary Files | Route(s) |
|---|---------|---------------|----------|
| 21 | Landing Explorer / Browse | `routes/browse.py`, `lineage_service.py` | `GET /api/catalogs`, `/api/schemas`, `/api/tables` |
| 22 | Deep-link Routing | `frontend/src/hooks/useRouter.ts` | `?table=`, `?view=`, `?admin=`, `?controlPanel=` |
| 23 | Three View Modes + Depth | `frontend/src/components/graph/` | client-side (Tables/Pipelines/Full + hop slider) |
| 24 | In-graph Tools (search, fit, drag) | `frontend/src/components/graph/` | client-side Cmd+K, fitView, drag |
| 25 | Cross-Schema/Catalog Enrichment | `lineage_service.py` | included in `/api/lineage` response |
| 26 | Expression-Level Transform | `transform_service.py`, `transformation_lineage/` | `GET /api/transform/{table}/{column}` |
| 27 | Transform Freshness + Builder | `build_service.py` | `POST /api/transform/build`, `GET /api/transform/status` |
| 28 | Transform Diagnostics | `transform_service.py` | `GET /api/transform/diagnose` |
| 29 | Delta Sharing Overlay | `lineage_service.py` → `get_sharing_overlay()` | included in lineage response |
| 30 | Serverless Cost on Nodes | `cost_service.py` | `GET /api/cost/{entity}` |
| 31 | Admin Ops Dashboard | `routes/admin.py` | `GET /api/admin/metrics`, `/api/cache/*` |
| 32 | Excel Export + Preview | `routes/export.py` | `GET /api/lineage/export` |
| 33 | Request Coalescing + Cache | `main.py` (middleware) | infra (no dedicated route) |
| 34 | Control Panel (flags) | `feature_flags.py`, `routes/control_panel.py` | `GET\|POST /api/control-panel/flags/*` |
| 35 | Runtime Plan Capture | `plan_capture/capture.py` | write path (pipeline notebooks only) |
| 36 | Captured-Plan Precedence | `plan_capture_service.py`, `sublineage/backtrack.py` | `GET /api/transform/captured-expression` |
| 37 | Federated Sync (peers) | `federated_sync.py` | `GET\|POST /api/control-panel/federated/*` |
| 38 | Federated Source Overlay | `federated_service.py` | `GET /api/lineage/federated-overlay` |
| 39 | LLM Producer Analysis | `routes/analyze_producer.py` | `POST /api/analyze-producer` |
| 40 | Pipeline Capture Installer | `routes/pipeline_installer.py` | `POST /api/pipeline/install-capture` |
| 41 | SCD/CDC Spec Viewer | `routes/diagnostics.py` | `GET /api/diagnostics/scd` |
| 42 | Schema-Change Detector | `routes/diagnostics.py` | `GET /api/diagnostics/schema-changes` |
| 43 | Column Profiling Overlay | `routes/diagnostics.py` | `GET /api/diagnostics/profile` |
| 44 | Billing per Feature Flag | `routes/diagnostics.py` | `GET /api/diagnostics/billing/{flag_id}` |
| 45 | Federated Trust Handshake | `routes/diagnostics.py` | `GET\|POST /api/diagnostics/federated/*` |
| 46 | PII/Sensitive Column Finder | `routes/discovery.py` | `GET /api/discover/sensitive` |
| 47 | Orphan Table Detector | `routes/discovery.py` | `GET /api/discover/orphans` |
| 48 | Sensitivity Propagation | `routes/governance.py` | included in governance response |

---

## Part C — Key Subsystems (for debugging)

| Subsystem | Entry Point | Notes |
|-----------|------------|-------|
| Plan Capture (write) | `backend/plan_capture/capture.py` | Runs inside pipeline notebooks, writes to `captured_plans` |
| Plan Parser | `backend/plan_capture/plan_parser.py` | Parent-chain tracking, multi-line folding, LEAF_PREFIXES, UDF detection |
| Plan Capture (read) | `backend/plan_capture_service.py` | Flag-gated; `get_captured_expression()` |
| Federated Overlay | `backend/federated_service.py` | `system.information_schema.connections` + foreign catalogs |
| Feature Flags | `backend/feature_flags.py` | Delta-backed toggles; env-var kill switches |
| Transform BFS | `backend/sublineage/backtrack.py` | Column-level upstream walk; captured-plan precedence |
| Build Pipeline | `notebooks/run_pipeline` → `transformation_lineage/` | Serverless job |
| Cost Service | `backend/cost_service.py` | 30-day billing from `system.billing` |
| Sharing Overlay | `backend/lineage_service.py` → `get_sharing_overlay()` | Delta Sharing boundary nodes |
| Control Panel | `backend/feature_flags.py` + `routes/control_panel.py` | Admin toggles UI |
| Root Cause + DQ | `backend/server/root_cause.py` | BFS walk → failed runs + DQ rule violations |
| Capability Closures | `backend/routes/capability_closures.py` | BI consumers, streaming topology, auto-snapshots, DQ trends, webhooks |
| OL Bridge | `backend/routes/external_sources.py` (ol-bridge section) | Register → receive URL → external platform pushes OL RunEvents → `external_ol_bridge_events` |
| Distributed Cache | `backend/cache_service.py` | Delta-table-backed shared cache; DeltaCacheService singleton; MERGE upsert + TTL expiry |
| Graph Pagination | `backend/routes/scalability.py` | BFS cursor-pagination (opaque base64 cursors), removes 400-node cap |
| Perf Patches | `backend/perf_patches.py` | Side-effect import: parallel list_all_tables, parallel BFS trace, parallel cost, Delta cache wiring |

---

## Part D — Closure History

### v2.5.4 — Scalability closure (#18)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #18 Scalability | PARTIAL | HAVE | Two blockers closed: (1) **Distributed cache** — `DeltaCacheService` in `cache_service.py` backs an all-replica shared Delta table; replicas read from it before hitting DBSQL, wired via `perf_patches.py` PATCH 5. No Redis/external infra required. (2) **Graph pagination** — `GET /api/scalability/graph` implements BFS cursor-pagination (page_size up to 1000, opaque continuation cursors); removes the 400-node hard cap. Scalability router registered in `main.py`. |

### v2.5.3 — OL producer bridge (#05 Multi-Platform)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #05 Multi-Platform | PARTIAL | HAVE | Added OL producer bridge: external OL-emitting platforms (Snowflake Horizon, BigQuery via OL proxy, Spark + openlineage-spark, Flink, dbt Cloud, Airflow 2.7+) register once and receive a push URL. Lineage flows into `external_ol_bridge_events` via `POST /api/external/ol-bridge/ingest/{source_id}`. No in-platform compute required — platforms own their OL emission; we own ingestion. |

### v2.5.2 — Bulk closure (7 items)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #01 Automatic Discovery | PARTIAL | HAVE | dbt/Airflow import + foreign catalog crawl + manual registration already covered all cases |
| #02 End-to-End Lineage | PARTIAL | HAVE | Added BI consumer detection (`/api/lineage/bi-consumers`) + streaming topology (`/api/lineage/streaming-topology`) |
| #07 Versioned Lineage | PARTIAL | HAVE | Added auto-capture scheduling (`/api/snapshots/auto-capture`) + growth timeline (`/api/snapshots/timeline`). Diff already existed. |
| #11 Data Quality | PARTIAL | HAVE | Added trend history (`/api/dq-rules/trends`) + metrics recording + pipeline expectation sync. Live metrics + propagation already existed. |
| #14 AI/ML Lineage | PARTIAL | HAVE | Feature tables + vector indexes + vector lineage + prompt lineage + model registry all already implemented in `routes/ml.py` |
| #20 Notifications | PARTIAL | HAVE | Added webhook registration + delivery queue. Detection scan (schema/DQ/sensitive) + alert rules already existed. |
| #10 Business Lineage | GAP | HAVE | Already had full glossary (term CRUD, domains, KPIs, term→table linking). Added downstream propagation suggestions (`/api/glossary/propagate-suggestions`) + lineage graph overlay (`/api/glossary/lineage-overlay`). |
| #17 Open Standards | PARTIAL | HAVE | Added live producer: configure endpoint → detect writes via `system.access.table_lineage` → queue OL RunEvents → async delivery. Bidirectional: export + import + produce. |

### v2.5.1 — Initial closures (3 items)

| Item | Was | Now | How |
|------|-----|-----|-----|
| #05 Multi-Platform | GAP | PARTIAL | Federated overlay for foreign catalogs |
| #09 Root Cause | PARTIAL | HAVE | DQ rule violation wired into `trace_root_cause()` |
| #17 Open Standards | GAP | PARTIAL | OpenLineage export + import endpoints |

### Remaining

None — all 20 scorecard capabilities are at HAVE status.

---

## Part E — Design Document Coverage

| Document | Covers | Status |
|----------|--------|--------|
| `lineage-capability-matrix.pdf` | 20-item scorecard | ✅ 20/20 HAVE, 0 PARTIAL, 0 GAP |
| `FEDERATED_LINEAGE_DESIGN.docx` | Tier 1–3 federation | Tier 1, Tier 2, Tier 3 (glossary implemented) |
| `Column Transformation Lineage — Design Document.pdf` | LLM expression inference | `POST /api/analyze-producer` |
| `lineage-plan-capture` project | Runtime capture + parser | `backend/plan_capture/` (mature parser) |
