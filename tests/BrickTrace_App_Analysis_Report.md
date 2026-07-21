# BrickTrace — Comprehensive App Analysis Report

**Product:** BrickTrace (lineage-explorer-transforms-feature-Lineage_App_Combined)  
**Perspective:** Databricks Apps deployment  
**Method:** Static code review of `backend/`, `frontend/`, `transformation_lineage/`, `docs/`, `databricks.yml`, `setup.sql`, `grant_app_access.sh`  
**Date:** July 21, 2026  
**Scope:** Bug analysis · Capability gap analysis · Edge-case analysis  

> **Verdict:** Core UC table/column lineage is real. Do **not** trust the scorecard claim of “20/20 HAVE” or “metadata-only / never row data” for a wide Apps rollout. Highest risks: SQL running as the App service principal with unsafe interpolation, ungated mutating/expensive APIs, OpenLineage UUID-as-auth, broken plan-capture installer for customer pipelines, and stale `frontend/dist`.

---

## Executive summary

| Category | Count / status |
|----------|----------------|
| Critical bugs | 3 |
| High bugs | 6 |
| Medium bugs | 5 |
| Low bugs | 1 |
| Capabilities truly HAVE | 3 of 13 audited families |
| Capabilities PARTIAL | 8 |
| Capabilities GAP | 2 (+ misleading scorecard) |
| Edge cases documented | 16 |

### How the Databricks App actually runs

1. User opens the App URL → Apps proxy may send `x-forwarded-access-token` (identity / admin group only).
2. FastAPI (`uvicorn backend.main:app`) serves API + `frontend/dist` SPA.
3. All SQL and Jobs use **`WorkspaceClient()` = App service principal**, not user OBO.
4. Transform builds submit serverless Jobs as the App SP.
5. Plan capture (optional) writes from **customer pipeline notebooks** into app-owned Delta (`lattice_lineage.lineage` by default).

**Implication:** Anyone who can open the App sees whatever the App SP can `BROWSE`/`SELECT`. The App ACL is the real perimeter—not per-user Unity Catalog ACLs.

---

## A. Bug analysis

### Critical

| ID | Title | Area | Evidence | Impact | Fix direction |
|----|-------|------|----------|--------|---------------|
| A1 | SQL injection via string interpolation | Security / Warehouse | `capability_closures.py`: `catalog`/`table` into `LIKE '%…%'` and `table_catalog = '{catalog}'`; `dq.py` CUSTOM/RANGE inject expressions; glossary/OpenLineage also interpolate | Any App user can craft SQL as the App SP on the bound warehouse | Parameterized SQL / `IDENTIFIER()`; whitelist AST for CUSTOM DQ; shared validators |
| A2 | Missing admin gates on mutating / expensive APIs | AuthZ | `POST /api/transform/build` ungated; glossary CRUD; notifications `/scan`; snapshots; external_sources import/OL bridge; OL producer; auto-capture | Any App opener can burn Jobs/warehouse $, rewrite glossary, inject lineage, spam scans | Admin-gate all writes and heavy scans; optional scheduler token for job-only routes |
| A15 | OpenLineage bridge UUID-as-auth | Security | `external_sources.py`: register returns `source_id`; ingest authenticates only by path UUID; register itself ungated | Leaked/guessed ID or any App user can push fake lineage events | High-entropy secret + HMAC; admin-only register; rotate tokens |

### High

| ID | Title | Area | Evidence | Impact | Fix direction |
|----|-------|------|----------|--------|---------------|
| A3 | BFS captured-plan override is dead code | Plan capture | `transform_service._get_captured_expression_for_node` defined, never called; README claims override; ARCHITECTURE correctly says additive API | Docs oversell; precedence is additive only (when UI ships) | Wire into BFS or delete helper and correct README/scorecard |
| A4 | Stale `frontend/dist`; orphan panels never mounted | Databricks Apps deploy | `databricks.yml` `sync.exclude` drops `frontend/src`; dist JS has no Control Panel / captured-expression; Glossary/DQ/Notifications/Export/RootCause never imported in `App.tsx` | Deployed users never see Control Panel or v2.5 product surfaces | Wire panels; rebuild dist; CI assert dist contains expected routes |
| A6 | Pipeline capture installer broken on Apps/Jobs | Plan capture / workflows | `pipeline_installer` injects `sys.path` `/Workspace/Users` + `from backend.plan_capture`; only pip-installs `databricks-sdk`; forces language PYTHON | Customer pipeline opt-in fails at import; capture never lands | Publish wheel / workspace library; resolve real app path; detect notebook language |
| A7 | Build notebook path auto-derive wrong for Apps | Transform builds | `build_service._derive_pipeline_notebook_path` uses `__file__` then prefixes `/Workspace` → nonsense container paths; default `pipeline_notebook_path` empty | Generate lineage submits Jobs that fail path/ACL | Require explicit `PIPELINE_NOTEBOOK_PATH`; fail closed if unset |
| A8 | `grant_app_access.sh` incomplete vs `setup.sql` | Post-deploy grants | Shell grants system schemas + optional BROWSE only; omits `lattice_lineage` create/privileges and `system.query` for BI | Following helper → graph may work; flags/DQ/transforms/BI fail | Align script with `setup.sql`; document mandatory multi-catalog BROWSE |
| A14 | `LOCAL_DEV_ADMIN_EMAIL` privilege escalation if set on App | Auth | `main.py`: no token + env set → `admin=True`. Not enforced off in prod target | Mis-set env makes every headerless request an admin | Assert unset at startup outside local; strip from prod env |

### Medium

| ID | Title | Area | Evidence | Impact | Fix direction |
|----|-------|------|----------|--------|---------------|
| A5 | Version numbers contradict across repo | Release hygiene | `APP_VERSION` 2.4.0; `package.json` 2.4.0; README badge 2.5.1; CHANGELOG 2.5.2 (claims bump); capability_code_map 2.5.4 | `/health` and ops trust broken | Single version source updated with every release |
| A9 | Tests assert wrong API contracts | Quality | `test_routes_capability_closures` expects list/`tool_type`/422; handlers return wrapped objects and optional params | False confidence; regressions slip | Rewrite tests to live contracts |
| A10 | Silent empty 200s mask grant/SQL failures | Observability | bi-consumers `except` → `{bi_consumers:[], note}`; streaming edge loop `pass` | Empty estate looks like “no BI tools” when `system.query` missing | Return `available:false` / 503 on infra failure |
| A11 | Rate limit collapses without user token | Multi-user Apps | Falls back to client IP / `unknown` behind Apps proxy | Shared bucket across users | Prefer forwarded email/token hash |
| A12 | No per-table build lock; cold cache on restart | Concurrency / cost | `submit_build_job` no mutex; lifespan `invalidate_cache` + billing prefetch | Duplicate Jobs for same FQN; warehouse spike on restart | Per-table lease; soft-warm |

### Low

| ID | Title | Area | Evidence | Impact | Fix direction |
|----|-------|------|----------|--------|---------------|
| A13 | Dual config: `app.yaml` vs `databricks.yml` | Deploy | Root `app.yaml` command-only; env only in DAB `resources.apps` config | Editing `app.yaml` alone ships App without warehouse env | Document DAB as source of truth |

---

## B. Capability gap analysis

**Legend**

- **HAVE** — Backend + meaningful UI in the App product path  
- **PARTIAL** — API (and maybe src UI) exists, but ungated, unwired, incomplete, or not in shipped `dist`  
- **GAP** — Claimed complete without a usable product path  

| Family | Claimed | Actual | Evidence |
|--------|---------|--------|----------|
| Core table/column lineage + browse | HAVE | **HAVE** | Primary App path: `lineage_service` + LineageCanvas + browse flows |
| Expression transform lineage | HAVE | **HAVE** | `transformation_lineage` engine + TransformPanel (src). Dist may lag |
| Control Panel | HAVE | **HAVE** | Wired in `App.tsx` + feature flags. Missing from shipped dist until rebuild |
| Runtime Plan Capture | HAVE | **PARTIAL** | Capture+parser solid; installer broken; toggle does not instrument pipelines |
| Captured-Plan Precedence | HAVE (BFS override) | **PARTIAL** | Additive API + src UI only; BFS override dead; dist missing UI |
| Federated Sync | HAVE | **PARTIAL** | Peer registry + trust probe; still scaffold; federated-overlay unwired in FE |
| Governance / Impact / Observability / Access / ML | HAVE | **PARTIAL** | Backend routes exist; no `App.tsx` product surfaces |
| Data Quality | HAVE | **PARTIAL** | API + orphaned `DQMetricsPanel`; needs catalog SELECT (breaks metadata-only) |
| Glossary / Notifications / OpenLineage UX | HAVE | **GAP** | Panels exist as files but never imported; mutations often ungated |
| BI consumers / Streaming topology | HAVE | **GAP** | API-only; silent failures; `system.query` not in grant script; no UI |
| Snapshots / versioned lineage | HAVE | **PARTIAL** | Capture APIs; ExportPanel orphaned; auto-capture ungated/expensive |
| Diagnostics / SCD | HAVE | **PARTIAL** | Routes exist; SCD depends on `capture_cdc_spec` opt-in; thin UI |
| Scorecard summary | 20/20 HAVE | **GAP** | Docs count APIs as product completeness; misleading for Apps rollout |

### Plan-capture plugin (customer workflows)

The **lineage-plan-capture** plugin is vendored as `backend/plan_capture/`. Intended flow:

```
Customer pipeline → capture(df, target) → captured_plans Delta
       → plan_capture_service (flag-gated) → GET /api/transform/captured-expression
       → TransformPanel (src only; not in dist)
```

| Piece | Status |
|-------|--------|
| Capture + plan parser | Solid (non-fatal, `explain(extended)`, hash dedup) |
| App read path + feature flags | Solid |
| Additive UI in `frontend/src` | Wired |
| Auto-inject into customer notebooks | **Not production-ready** |
| BFS override claimed in README | **Dead code** |
| Shipped App (`frontend/dist`) | **Missing** plan-capture UI |

---

## C. Edge-case analysis

| ID | Severity | Edge case | Observed behavior | Mitigation |
|----|----------|-----------|-------------------|------------|
| C1 | High | System tables disabled / SP grants missing | Empty graph; BI returns empty 200; diagnostics explains if checked | Surface grant failures in UI; never cache empties as success |
| C2 | High | Incomplete cross-catalog BROWSE | Lineage cone silently truncated | Detect partial cones; require BROWSE on all visible catalogs |
| C9 | High | Multi-user shared App SP visibility | SQL always App SP — not human UC ACLs | Document; optional OBO; don’t claim per-user enforcement |
| C10 | High | DQ / profiling without catalog SELECT | Metrics fail; with SELECT, CUSTOM expr = SP row access | Preflight privilege check; keep SELECT off unless isolation accepted |
| C6 | High | Concurrent transform builds same table | Multiple Jobs race writing same lineage tables | Per-FQN lease / reject if run in progress |
| C8 | High | Warehouse stopped / SQL timeout | 50s wait → 500 or swallowed empties | Circuit breaker + warehouse status in UI |
| C4 | Medium | Large graphs / memory bounds | `LINEAGE_MAX_NODES` ~2500; cache ~250MB; truncation possible | Always show truncated flag; paginated APIs for huge scopes |
| C5 | Medium | Producer outside discovery lookback | Empty transform lineage; diagnose explains age window | Keep diagnose prominent; align UI with `DISCOVERY_LOOKBACK_HOURS` |
| C7 | Medium | `feature_flags` table missing | Flags default OFF — safe but quiet | Control Panel banner when flags store unreachable |
| C11 | Medium | Capture when flags off / lineage schema missing | Read returns null; writes need CREATE on `lattice_lineage` | Installer + flag UX must check privileges |
| C12 | Medium | Non-Python notebooks for installer | Claims Python-only but may force language PYTHON | Reject non-Python exports; document unsupported |
| C15 | Medium | Hyphenated / special UC identifiers | `main._IDENTIFIER_RE` rejects hyphens; other validators allow | One shared UC identifier policy; backtick-quote |
| C14 | Medium | App restart cold cache + billing prefetch | Invalidate + heavy `system.billing` on every restart | Soft-warm; stagger prefetch |
| C3 | Medium | Delta Sharing / foreign catalog boundaries | Honest skip; incomplete without grants/overlay UI | Explicit boundary nodes; don’t claim full multi-platform |
| C16 | Medium | SCD/CDC without `capture_cdc_spec` | APPLY CHANGES not auto-discovered | Document opt-in; optional DLT detection |
| C13 | Low | Serverless Spark Connect vs classic capture | `explain(extended)` for both; Connect confs may be empty | Keep Method B; serverless test matrix |

### What breaks on `bundle deploy` / `bundle run`

| Action | Typical failure |
|--------|-----------------|
| Deploy without `--var warehouse_id` | Incomplete App / no warehouse |
| Deploying user lacks warehouse `CAN_MANAGE` | Cannot attach sql-warehouse resource |
| Skip grants / only run shell helper | Empty graph and/or broken UC app features |
| System tables not enabled at account | Empty lineage regardless of grants |
| No `pipeline_notebook_path` | Transform build job path wrong |
| UI change without rebuilding `dist` | Users see old SPA |
| Grant catalog `SELECT` for DQ | Breaks metadata-only isolation story |
| Set `LOCAL_DEV_ADMIN_EMAIL` on App | Everyone without token treated as admin |

---

## D. Recommended remediation order

1. **Security first** — Parameterize SQL; admin-gate builds/imports/scans/OL; replace UUID bridge auth; block `LOCAL_DEV_ADMIN_EMAIL` in prod.
2. **Apps deploy honesty** — Rebuild `frontend/dist`; CI-check route strings; require `PIPELINE_NOTEBOOK_PATH`; extend `grant_app_access.sh` to match `setup.sql`.
3. **Plan capture into real workflows** — Ship capture as wheel/workspace lib; fix installer import path; either wire BFS override or remove the claim.
4. **Product completeness** — Wire or demote scorecard items; mount orphan panels or remove them; fix capability_closures tests.
5. **Ops resilience** — Stop silent empty 200s; per-table build locks; warehouse circuit breaker; soft cache warm on restart.

### Admin checklist before wide Apps rollout

1. Scope App SP `BROWSE` tightly; avoid catalog `SELECT` unless DQ row sampling is accepted.
2. Run full `setup.sql`, not only `grant_app_access.sh`.
3. Set explicit `pipeline_notebook_path`; grant Jobs + notebook execute to App SP.
4. Gate who can open the App — code does not enforce per-user UC on SQL.
5. Rebuild and commit `frontend/dist` before UI deploys.
6. Never set `LOCAL_DEV_ADMIN_EMAIL` on the deployed App.

---

## Appendix — Key file references

| Topic | Paths |
|-------|-------|
| App entry / auth / transform build | `backend/main.py` |
| Lineage SQL (App SP client) | `backend/lineage_service.py` |
| Plan capture write | `backend/plan_capture/capture.py`, `plan_parser.py` |
| Plan capture read | `backend/plan_capture_service.py` |
| Pipeline installer | `backend/routes/pipeline_installer.py` |
| Capability closures / BI / streaming | `backend/routes/capability_closures.py` |
| DQ metrics / CUSTOM SQL | `backend/routes/dq.py` |
| Build job path | `backend/build_service.py` |
| Feature flags | `backend/feature_flags.py` |
| Deploy / sync exclude | `databricks.yml` |
| Grants | `setup.sql`, `grant_app_access.sh` |
| Shipped UI | `frontend/dist/` (src excluded from sync) |
| Scorecard | `docs/capability_code_map.md`, `README.md`, `CHANGELOG.md` |

---

*End of report. This is a static audit, not a live penetration test or workspace `/api/diagnostics` run.*
