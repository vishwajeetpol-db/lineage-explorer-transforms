# Capabilities Catalog

Every capability BrickTrace ships, grouped by whether it's always on or
gated behind the [Control Panel](#control-panel) (added in 2.4.0). "Always on"
capabilities need no setup beyond the base deploy in [README.md](../README.md).
Gated capabilities are OFF by default for every new deploy and every existing
deploy that upgrades to 2.4.0 — enabling one is an explicit admin action.

## Always-on capabilities

| Capability | What it does | Where |
|---|---|---|
| Table & column lineage | Cross-catalog trace from `system.access.table_lineage` / `column_lineage` | `lineage_service.py` |
| Expression-level transformation lineage | Parses the actual SQL/PySpark that produced a column | `transformation_lineage/`, `transform_service.py` |
| Delta Sharing overlay | Shared-out/shared-in boundary nodes on the graph | `lineage_service.get_sharing_overlay` |
| Serverless cost on entity nodes | 30-day list price from `system.billing` | `lineage_service.py` (`_cost_by_job_id`/`_cost_by_pipeline_id`) |
| Excel export | Styled multi-sheet `.xlsx` of the current graph | `excel_export.py` |
| Admin ops dashboard | Latency/memory/cache/thread-pool metrics | `AdminDashboard.tsx`, `/api/admin/status` |
| Live mode | Admin-only cache bypass, direct system-table reads | `?live=true` internally, gated to `ADMIN_GROUP_NAME` |
| Table Lineage workspace (v2.5.5) | Per-table analysis workspace: catalog tree + lineage graph + draggable panels for Impact, Root Cause, Governance, Access, ML Models, Column Transformation | `frontend/src/components/table-lineage/`, `?view=tableLineage` — see [capability_code_map.md](capability_code_map.md) Part F |
| Light / dark theme (v2.5.5) | Theme toggle (top-right); CSS-variable tokens flip the whole UI; persisted | `store/themeStore.ts`, `components/ui/ThemeToggle.tsx`, `styles/globals.css` |
| Cached capability panels (v2.6.0) | Impact/Root Cause/Governance/Access cached per table in Delta; "cached / may be stale" badge + refresh; admin evict per entry/table/all | `server/capability_cache.py`, `/api/admin/capability-cache` — see [capability_code_map.md](capability_code_map.md) Part G |
| Per-node run health check (v2.6.0) | Activity icon on every job/pipeline node → verdict + success rate + duration trend + cost total/spike + last-5 runs (per-run cost + deep links) | `server/observability.py` `get_recent_runs`, `/api/observability/runs`, `EntityNode.tsx` |
| Multi-producer transformation comparison (v2.6.0) | Side-by-side per-column matrix when a table has 2+ producers, flagging divergent logic | `producer_source.compare_producers`, `/api/column-transformations/compare-producers` |
| Business view (v2.6.x) | Canvas **Technical ⇄ Business** toggle: relabels technical types to business terms, humanizes `snake_case` names, shows a plain-English description per dataset, hides FQNs/columns/IDs/cost/health; persisted | `frontend/src/lib/businessView.ts`, `store/lineageStore.ts` (`businessView`) — see [capability_code_map.md](capability_code_map.md) Part H |
| Data-only vs Data + processing (v2.6.x) | Business-view sub-toggle: datasets only, or datasets + the jobs/pipelines that move data between them; data-only renders **precise** `system.access.table_lineage` pairs (no cross-product mesh) | `businessDetail` in `lineageStore`, `LineageResponse.table_edges`, `/api/lineage/trace` |
| AI "Explain this lineage" (v2.6.x) | Business-view lightbulb → modal with a plain-English summary + ordered source→process→output walkthrough of the current on-screen graph | `llm.explain_lineage_graph`, `POST /api/lineage/explain`, `components/graph/LineageExplainModal.tsx` |
| Notifications + background auto-scan (v2.6.x) | Home "Recent Activity" and the bell feed. A periodic detection scan runs in the background (off the request path) so notifications populate on their own: **schema changes** (tables altered <24h, excluding platform/app-owned schemas), **sensitive flows** (PII-named columns flowing to a target with **no UC classification tag**), and **DQ degradation** (ERROR-severity rules). Scans dedup by natural key and prune to a retention cap. Env: `NOTIFICATION_AUTOSCAN_ENABLED`, `NOTIFICATION_SCAN_INTERVAL_SECONDS`, `NOTIFICATION_SCAN_INITIAL_DELAY_SECONDS`, `NOTIFICATION_RETENTION_MAX`. Admins can still trigger a scan manually. | `backend/routes/notifications.py` (`run_scan`, `_autoscan_loop`), `/api/notifications/*` |

## Control Panel

Opens from the header menu (☰ → **Control Panel**) or via `?controlPanel=true`.
Visible to every authenticated user (read-only — see each capability's
description, cost/risk, and access requirements); **toggling a capability
requires workspace admin** (member of `ADMIN_GROUP_NAME`, default `admins`).

Each capability card shows:
* **Cost** and **risk** badges (low/medium/high) — editorial ratings, not a
  live billing signal yet (see [architecture.md](architecture.md) §5).
* **Side effects** — the concrete, specific things enabling it changes (extra
  writes, extra queries, new tables it creates).
* **Access requirements** — the exact UC privileges/scopes needed, with a
  "Run live access check" button that does a best-effort verification
  (`GET /api/control-panel/access-check/{flag_id}`) against the current
  identity.
* A **kill-switched** badge if an ops-level env var
  (`ENABLE_PLAN_CAPTURE` / `ENABLE_CAPTURED_PLAN_PRECEDENCE` /
  `ENABLE_FEDERATED_SYNC`) is forcing the capability off regardless of the
  toggle state — the switch is disabled in that case.

### Lineage Tracking → Runtime Plan Capture

**What it is.** Captures the *exact* per-column expression Spark actually ran
(`df.explain(mode="extended")`, works on classic Spark and serverless Spark
Connect) — for wheel-based or dynamically-built DataFrames the static
SQL/PySpark parser can't read from source code, this is the only way to get
exact transformation logic.

**Enabling it (Control Panel toggle) makes the app start *reading*
`captured_plans`/`captured_cdc_specs` — it does not, by itself, start
*writing* anything.** Writing only happens inside a pipeline you explicitly
opt in, by hand, with two additions to the pipeline notebook, illustrated
below (not literal copy-paste — adapt table names and write mode to your
pipeline):

```python
# Cell 1 — install the vendored capture module onto the pipeline's cluster.
# (In this combined-app deploy, backend/plan_capture/ ships with the app;
#  for a standalone pipeline notebook, package it as a wheel or copy the
#  two files into a repo the pipeline can import from.)
%pip install databricks-sdk  # if not already present

# Cell 2 — right before your pipeline writes result_df to its target table,
# capture its plan. capture() is non-fatal — it never raises, and it does not
# perform the write itself; your existing write call is unchanged.
from backend.plan_capture import capture

capture(result_df, target="catalog.schema.table")
# ... your pipeline's existing write call continues exactly as before ...
```

For an `APPLY CHANGES`/AUTO CDC target (no plan — it's config, not a query),
use `capture_cdc_spec()` instead:

```python
from backend.plan_capture import capture_cdc_spec
capture_cdc_spec(
    target="catalog.schema.scd_table",
    source="catalog.schema.cdc_source",
    keys=["id"],
    sequence_by="updated_at",
    scd_type=2,
)
```

**Status card** (`GET /api/control-panel/plan-capture/status`) shows captured
plan/CDC-spec counts and distinct target tables — a quick way to confirm a
pipeline's capture calls are actually landing rows before relying on them.

**Access requirements**: `WRITE VOLUME` on the app's wheel-staging volume
(only if you package as a wheel rather than importing the module directly),
`CREATE TABLE` on the app-owned lineage schema (auto-created on first write),
and `CAN_MANAGE`/`CAN_MANAGE_RUN` on the target job/pipeline (to add the two
cells above).

### Column Transformation → Captured-Plan Precedence

**Depends on Runtime Plan Capture being enabled too** (the Control Panel
disables this toggle if the dependency isn't already on). When both are
enabled, opening a column's transformation drill-down additionally calls
`GET /api/transform/captured-expression` and — if a captured plan exists for
that exact column — shows it labeled "Runtime-captured" with its own
confidence score, alongside the existing static-parse expression. See
[architecture.md](architecture.md) §3 for the "additive, not a BFS override"
scope decision.

**Access requirements**: `SELECT` on `captured_plans` (same table Runtime
Plan Capture already writes to).

### Federated Sync → Cross-Workspace Lineage Sync

**What it is today**: an admin-curated registry of "known peer"
workspaces/metastores (`POST /api/control-panel/federated/peers` with body
`{peer_alias, share_name, direction, notes}`), cross-referenced against the
Delta Sharing metadata the app already reads, so a shared boundary node can be
labeled as a known peer instead of an anonymous share.

**Status card** (`GET /api/control-panel/federated/status`) shows registered
peer count vs. how many resolve to a share the sharing overview can currently
see (`reachable_overlap`).

**What it is NOT yet** (see [architecture.md](architecture.md) §5 Known Gaps):
live cross-workspace API calls, peer trust handshakes, or peer-initiated sync
jobs. It changes only what a boundary node's *label* says, never what data
crosses the metastore boundary — the app still reads only metadata, per the
project's zero-row-access design.

**Access requirements**: `SELECT` on `system.information_schema` sharing views
(already required for the base Delta Sharing overlay — no new grant), plus
`CREATE TABLE` on the app-owned schema for the registry table.

## Where each flag's ID appears in the API

| Flag ID | Used by |
|---|---|
| `lineage_tracking.plan_capture` | `plan_capture_service.py` (all functions) |
| `column_transformation.captured_plan_precedence` | `GET /api/transform/captured-expression` (frontend gates the call client-side via `useFeatureFlagEnabled`) |
| `federated_sync.cross_workspace` | `federated_sync.py` (all functions) |
