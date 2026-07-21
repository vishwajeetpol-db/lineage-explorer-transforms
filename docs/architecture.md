# Architecture — BrickTrace (Combined App)

Version 2.4.0. This document covers the combined app's architecture end to end,
with focus on the three capabilities added in 2.4.0: **Control Panel**,
**Runtime Plan Capture**, and **Federated Sync**. For the pre-existing table/
column/transformation lineage engine, see [REFERENCE.md](REFERENCE.md) and
[DESIGN.md](DESIGN.md) — this document does not repeat that material except
where the new capabilities touch it.

## 1. System overview

Single-process FastAPI app, deployed as a Databricks App via Declarative
Automation Bundles. React SPA served as static files from the same process.

```
┌─────────────────────────────────────────────────────────────────┐
│ Databricks App (single process, uvicorn, 64-thread pool)        │
│                                                                   │
│  frontend/dist/  ──served by──▶  backend/main.py (FastAPI)       │
│                                        │                          │
│         ┌──────────────────────────────┼───────────────────────┐ │
│         ▼                              ▼                       ▼ │
│  lineage_service.py           transform_service.py     feature_flags.py
│  (table/column lineage,       (expression-level         (Control Panel
│   Delta Sharing, cost)         transformation graph)     registry)      │
│         │                              │                       │       │
│         │                              │              ┌────────┴─────┐ │
│         │                              │              ▼              ▼ │
│         │                              │   plan_capture_service.py  federated_sync.py
│         │                              │   (reads captured_plans)   (reads federated_peers +
│         │                              │                             sharing overview)       │
└─────────┼──────────────────────────────┼──────────────┼──────────────┼─┘
          ▼                              ▼              ▼              ▼
   system.access / billing /    lattice_lineage.lineage (app-owned Delta schema:
   information_schema           transform_edges, captured_plans, captured_cdc_specs,
   (Unity Catalog system        feature_flags, feature_flags_audit, federated_peers)
   tables — read-only)
```

Everything the app itself writes lives in one app-owned schema
(`LINEAGE_CATALOG.LINEAGE_SCHEMA`, default `lattice_lineage.lineage`) — never
in a user's data catalog. This was already true for the transformation-lineage
tables; the three new tables (`feature_flags`, `feature_flags_audit`,
`federated_peers`) plus the plan-capture tables (`captured_plans`,
`captured_cdc_specs`) follow the same convention.

## 2. New in 2.4.0 — Control Panel

`backend/feature_flags.py` is a small Delta-backed registry: a static Python
list (`FLAG_DEFINITIONS`) describing each optional capability's cost, risk,
side effects, and access requirements, joined at read time with a persisted
`enabled` bit per flag in `feature_flags` (survives redeploys/restarts, unlike
an in-process toggle).

**Effective state = persisted DB flag AND an ops-level env-var kill switch**
(`ENABLE_PLAN_CAPTURE`, `ENABLE_CAPTURED_PLAN_PRECEDENCE`, `ENABLE_FEDERATED_SYNC`,
all default `true`/allow). The kill switch exists so an operator can force a
capability off instantly (e.g. a misbehaving pipeline job) without depending on
the SQL warehouse being reachable — it's checked in Python before any SQL runs.

Every flag defaults to **disabled** until an admin turns it on from the
Control Panel UI (`frontend/src/components/control-panel/`, opened via the
header menu or `?controlPanel=true`). Reading the flag list and each flag's
static metadata is open to any authenticated user; toggling a flag
(`POST /api/control-panel/flags/{flag_id}`) and registering a Federated Sync
peer are admin-gated the same way `/api/cache/invalidate` already is.

`check_access_requirements()` does a **best-effort live check** — e.g. `SHOW
GRANTS ON SCHEMA` for a CREATE TABLE requirement, or a 1-row `SELECT` probe for
a SELECT requirement — and returns `satisfied: null` when it can't verify
automatically, rather than blocking the UI. This is explicitly *advisory*, not
an authorization gate; enabling a flag does not depend on the check passing.

## 3. New in 2.4.0 — Runtime Plan Capture (`lineage_tracking` module)

Vendored from the separate `lineage-plan-capture` wheel project into
`backend/plan_capture/` (`capture.py`, `plan_parser.py`, `__init__.py`),
adapted so `DEFAULT_TABLE`/`DEFAULT_SPEC_TABLE` derive from this app's
`LINEAGE_CATALOG`/`LINEAGE_SCHEMA` convention instead of the source project's
hardcoded catalog.

This module has **two independent halves that must not be confused**:

* **Write path (`backend/plan_capture/capture.py`)** — runs *inside a Lakeflow
  Job/Pipeline notebook*, not inside this app's process. It calls
  `df.explain(mode="extended")` (works on classic Spark AND serverless Spark
  Connect — the one extraction method proven to work on both) right before a
  write, hashes and dedupes the plan, and appends it to `captured_plans`. It is
  non-fatal by design — any exception is caught and returned as a status dict,
  never raised, so a broken capture call can never break the underlying
  pipeline write. **The app does not, and cannot, install this into a
  pipeline automatically** — see §5 Known Gaps.
* **Read path (`backend/plan_capture_service.py`)** — runs inside this app,
  gated by `get_flag_state("lineage_tracking.plan_capture")`. It only ever
  reads `captured_plans`/`captured_cdc_specs`; it never triggers a capture.
  `get_captured_expression()` fetches the latest plan for a target table,
  parses it with `plan_capture/plan_parser.py` (pure stdlib — the same parser
  vendored unchanged, no PySpark dependency), and matches a column.

### Captured-Plan Precedence (`column_transformation` module)

A second, dependent flag (`column_transformation.captured_plan_precedence`,
`depends_on: ["lineage_tracking.plan_capture"]`) controls whether the frontend
surfaces the captured-plan expression alongside the existing static-parse
expression in the transformation drill-down. **Scope decision**: this is
implemented as an *additive enrichment* — a separate endpoint
(`GET /api/transform/captured-expression`) the frontend calls alongside the
existing `/api/transform/trace` call, not a replacement of
`transform_service.backtrack_transform_lineage()`'s internals. Deep precedence
inside the BFS trace itself (so a captured expression could *change* which
upstream columns the graph walks) is a follow-up — see §5.

## 4. New in 2.4.0 — Federated Sync (`federated_sync` module)

`backend/federated_sync.py` adds an admin-curated registry
(`federated_peers`: peer_alias, share_name, direction, registered_by/at,
notes) and cross-references it against the **existing** Delta Sharing overview
(`lineage_service.get_sharing_overview()`) to report how many registered peers
resolve to a share/foreign-catalog the app can already see.

## 5. Known Gaps (explicit, not swept under the rug)

* **Federated Sync is a v1 scaffold, not a live sync mechanism.** The
  `FEDERATED_LINEAGE_DESIGN` document referenced for this feature was provided
  only as a binary `.docx` that could not be read during this implementation
  pass. What's built: an admin-curated peer registry cross-referenced against
  existing sharing metadata. What's **not** built: live cross-workspace API
  calls, trust handshakes/attestation, or peer-initiated sync jobs. Treat the
  registry as informational, not a trust boundary.
* **Runtime Plan Capture has no automated installer.** Opting a pipeline into
  capture (see [capabilites.md](capabilites.md)) is currently a manual,
  documented step (add a `%pip install` cell + one `capture(df, target)`
  call). An admin-facing "install capture into pipeline X" action was
  considered but deferred — it would need `CAN_MANAGE` on the target job/
  pipeline and notebook-mutation logic that didn't fit this pass's scope.
* **Captured-Plan Precedence is additive, not a BFS-path override.** See §3 —
  the captured expression is shown next to, not instead of, the static-parse
  result. Making it override which upstream columns the transformation graph
  walks would require changing `transform_service.backtrack_transform_lineage()`
  and was judged too risky to do without a fuller test pass against the
  existing transformation lineage test suite (`backend/tests/test_lineage_parsing.py`).
* **`check_access_requirements()` coverage is partial.** It can verify
  `CREATE TABLE` (via `SHOW GRANTS ON SCHEMA`) and `SELECT` (via a 1-row probe)
  requirements; `WRITE VOLUME`, `CAN_MANAGE`, and "Workspace Admin" requirements
  always return `satisfied: null` (unverifiable) today — see
  [testing_plan_for_Combined_App.md](testing_plan_for_Combined_App.md) for the
  plan to close this.
* **Control Panel does not yet have a Toolbar quick-action entry.** It's
  reachable from the header menu (`HeaderMenu.tsx`) or by deep link
  (`?controlPanel=true`), matching the existing Admin Dashboard's discovery
  pattern, but was not added to `Toolbar.tsx`'s primary action row — that file
  is large enough that a full-file edit felt like unnecessary risk for a
  cosmetic placement change in this pass.
* **Cost metadata for flags is a static editorial rating**, not yet backed by
  a live `system.billing` signal (unlike the existing per-entity cost cache in
  `lineage_service.py`). Once Runtime Plan Capture has run long enough to
  generate billable job runs, wiring `list_flags()` to a real cost estimate is
  a natural follow-up.

## 6. Directory map (new/changed files only)

```
backend/
  feature_flags.py            NEW  — Control Panel flag registry + admin-gated set/check
  plan_capture_service.py     NEW  — app-side read path for captured plans (gated)
  plan_capture/                NEW  — vendored write-path plugin
    __init__.py
    capture.py                      (adapted: LINEAGE_CATALOG/LINEAGE_SCHEMA defaults)
    plan_parser.py                  (vendored unchanged — pure stdlib)
  federated_sync.py           NEW  — peer registry + sharing-overview cross-reference
  models.py                   CHANGED — + FeatureFlagCard, AccessRequirement, PlanCaptureStatus,
                                          FederatedPeer, FederatedSyncStatus
  main.py                     CHANGED — APP_VERSION → 2.4.0; + /api/control-panel/*,
                                          /api/transform/captured-expression

frontend/src/
  api/controlPanel.ts          NEW  — Control Panel API client
  api/transform.ts             CHANGED — + getCapturedExpression()
  store/featureFlagStore.ts    NEW  — Zustand store for flags + module status cards
  components/control-panel/    NEW  — ControlPanel.tsx, ModuleSection.tsx,
                                        FeatureToggleCard.tsx, ImpactBadges.tsx,
                                        AccessRequirementsModal.tsx
  hooks/useRouter.ts           CHANGED — + "controlPanel" route (?controlPanel=true)
  components/layout/HeaderMenu.tsx  CHANGED — + "Control Panel" menu entry
  App.tsx                      CHANGED — + controlPanel route branch
  package.json                 CHANGED — version → 2.4.0 (was out of sync with CHANGELOG)

docs/
  architecture.md               NEW  — this file
  capabilites.md                NEW  — capability catalog + opt-in steps
  testing_plan_for_Combined_App.md  NEW — test plan for the 2.4.0 additions
```

## 7. Accent color conventions

Established for the app's dark theme (`#0A0A0F`/`#14141F`/`#1A1A2E`/`#1E1E2E`
surfaces, `#6366F1` indigo primary accent):

| Module | Accent | Rationale |
|---|---|---|
| Lineage Tracking (Plan Capture) | amber | distinct from existing indigo/emerald, signals "instrumentation" |
| Column Transformation (Captured-Plan Precedence) | violet | matches the Control Panel shell's own violet theme |
| Federated Sync | cyan | distinct, evokes "network/cross-boundary" |
| Control Panel shell | violet | deliberately distinct from AdminDashboard's emerald ops theme |
| Admin Dashboard (pre-existing) | emerald | unchanged |
