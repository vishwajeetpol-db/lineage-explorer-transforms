# Capability → Code Map (Debug Reference)

> **Purpose**: [docs/capabilites.md](capabilites.md) explains *what* each capability does.
> This doc exists so that when a capability misbehaves in the running app, you can jump
> straight to the exact backend route, service function, frontend component, and data
> table responsible — without grepping the whole tree.
>
> This is now the **implemented capability-family map for the combined app**, not just the
> original 12-module addendum. It covers the broader app surfaces that were folded into
> `/Workspace/Users/ayaskanta.ratha@databricks.com/lineage_app/lineage-explorer-transforms_combined`,
> including browse flows, scoped lineage entrypoints, in-graph controls, and the LLM-backed
> PySpark-expression enrichment stage used by transformation lineage builds.
>
> Every entry below is a real snippet (route decorator + signature, or the top of the
> function) copied verbatim from the current source, not a paraphrase — line numbers are
> current as of v2.4.0 but will drift as the files change; if a snippet doesn't match, the
> function name/route path is still the reliable anchor to `grep -n` for.

## Quick index

| # | Capability | Backend route(s) | Backend service fn | Frontend entry | Gated by flag? |
|---|---|---|---|---|---|
| 1 | Table & column lineage | `GET /api/lineage`, `/api/lineage/trace`, `/api/column-lineage`, `/api/schema-column-lineage` | `lineage_service.get_table_lineage/get_lineage_trace/get_column_lineage` | `store/lineageStore.ts`, `components/graph/LineageCanvas.tsx` | No |
| 2 | Expression-level transformation lineage | `GET /api/transform/trace` | `transform_service.backtrack_transform_lineage` | `store/transformStore.ts`, `components/transform/TransformPanel.tsx` | No |
| 3 | Transformation diagnostics | `GET /api/transform/diagnose` | `transform_service.diagnose_missing_lineage` | `components/transform/TransformPanel.tsx` (empty-state) | No |
| 4 | Lineage Builder (on-demand build jobs) | `POST /api/transform/build`, `GET /api/transform/status/{run_id}`, `GET /api/transform/build-configured` | `build_service.submit_build_job/get_build_status/is_build_configured` | `components/transform/BuildProgress.tsx` | No (env-configured) |
| 5 | Delta Sharing overlay | `GET /api/sharing/overlay`, `/api/sharing/overview` | `lineage_service.get_sharing_overlay/get_sharing_overview` | `components/graph/SharingNode.tsx` | No |
| 6 | Serverless cost on nodes | *(embedded in `/api/lineage*` responses)* | `lineage_service._entity_cost`, `_refresh_cost_cache` | `components/graph/EntityNode.tsx` (`cost_usd`) | No |
| 7 | Excel export | `GET /api/lineage/export` | `excel_export.build_lineage_workbook` | `api/client.ts → api.lineageExportUrl` | No |
| 8 | Admin ops dashboard / live mode | `GET /api/admin/status`, `POST /api/admin/evict-cache`, `POST /api/cache/invalidate` | `lineage_service.get_cache_snapshot`, in-`main.py` metrics globals | `components/AdminDashboard.tsx` | Admin-gated |
| 9 | Control Panel (flag registry) | `GET /api/control-panel/flags`, `POST /api/control-panel/flags/{flag_id}`, `GET /api/control-panel/access-check/{flag_id}` | `feature_flags.list_flags/set_flag_state/check_access_requirements` | `components/control-panel/ControlPanel.tsx`, `store/featureFlagStore.ts` | — (this *is* the flag system) |
| 10 | Runtime Plan Capture plugin integration | `GET /api/control-panel/plan-capture/status` (read); write path is out-of-process | `plan_capture_service.get_plan_capture_status`; `plan_capture/capture.py:capture()` | `components/control-panel/ControlPanel.tsx` (status card) | `lineage_tracking.plan_capture` |
| 11 | Captured-Plan Precedence | `GET /api/transform/captured-expression` | `plan_capture_service.get_captured_expression` | `store/transformStore.ts`, `components/transform/TransformPanel.tsx`, `api/transform.ts` | `column_transformation.captured_plan_precedence` |
| 12 | Federated Sync | `GET /api/control-panel/federated/status`, `/federated/peers`, `POST /federated/peers` | `federated_sync.get_federated_sync_status/list_federated_peers/register_federated_peer` | `components/control-panel/ControlPanel.tsx` (status card) | `federated_sync.cross_workspace` |
| 13 | LLM-assisted PySpark → SQL enrichment | *(pipeline build phase; no direct app route)* | `expression_enricher.enrich_pyspark_expressions` | surfaced through `components/transform/TransformPanel.tsx` after `getTransformTrace` | No (best-effort during build) |
| 14 | Landing explorer & browse flows | `GET /api/tables`, `/api/catalogs`, `/api/schemas`, `/api/sharing/overview` | `lineage_service.list_all_tables/list_catalogs/list_schemas/get_sharing_overview` | `components/landing/Landing.tsx`, `components/browse/*` | No |
| 15 | Deep-link routing & scoped lineage entrypoints | `GET /api/lineage`, `GET /api/lineage/trace` | `lineage_service.get_table_lineage/get_lineage_trace` | `hooks/useRouter.ts`, `App.tsx`, `components/landing/LineagePicker.tsx` | No |
| 16 | In-graph exploration controls | *(frontend-only; consumes `/api/lineage*`, `/api/column-lineage`, `/api/sharing/overlay`)* | — | `components/graph/LineageCanvas.tsx`, `components/layout/Toolbar.tsx`, `components/ui/SearchDialog.tsx` | No |

---

## 1. Table & column lineage

**What breaks visibly**: empty graph, missing edges, a table showing as an isolated node.

Backend — `backend/main.py`:
```python
@app.get("/api/lineage")
async def api_get_lineage(request: Request, catalog: str = Query(...), schema: str | None = Query(None), live: bool = Query(False)):
    catalog = _validate_identifier(catalog, "catalog")
    # schema is optional: omitting it builds catalog-wide lineage across all schemas
    ...

@app.get("/api/lineage/trace")
async def api_lineage_trace(request: Request, table: str = Query(...), live: bool = Query(False)):
    """End-to-end cross-catalog lineage trace from a single seed table.
    Walks system.access.table_lineage in both directions across all catalogs."""
```

Backend — `backend/lineage_service.py` (the actual UC system-table reads live here):
```python
def get_table_lineage(catalog: str, schema: str | None = None, skip_cache: bool = False) -> LineageResponse: ...
def get_lineage_trace(seed_full_name: str, skip_cache: bool = False) -> LineageResponse: ...
def _fetch_lineage_trace(seed_full_name: str) -> LineageResponse: ...   # actual BFS over table_lineage
def _build_graph_from_rows(client, lineage_rows: list[dict], truncated: bool = False) -> LineageResponse: ...
def get_column_lineage(catalog, schema, table, column, skip_cache=False) -> ColumnLineageResponse: ...
def get_schema_column_lineage(catalog, schema, skip_cache=False) -> ColumnLineageResponse: ...
```

Frontend: `frontend/src/store/lineageStore.ts` (Zustand graph state) → `frontend/src/components/graph/LineageCanvas.tsx` (ReactFlow render) → `frontend/src/api/client.ts` (`api.getLineage`, `api.getCatalogLineage`, `api.getLineageTrace`, `api.getColumnLineage`, `api.getSchemaColumnLineage`).

**Debug checklist**:
* Hit `GET /api/diagnostics` first — it directly reports whether `system.access` is readable by the SP. An empty graph is almost always a permissions/system-table problem, not a code bug.
* `_fetch_lineage_trace` and `_build_graph_from_rows` are where node/edge shape bugs live — check `truncated` in the response if a large trace looks cut off (node cap hit).
* Cache: `lineage_service._cache_get` / `_cached_fetch` — pass `live=true` on the route (admin-gated) to bypass and see if it's a stale-cache vs. a real-data issue.

---

## 2. Expression-level transformation lineage

**What breaks visibly**: clicking a column shows "not generated yet" for a table you expect to have lineage, or an expression looks wrong/truncated.

Backend — `backend/main.py`:
```python
@app.get("/api/transform/trace")
async def api_transform_trace(
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    ...
```

Backend — `backend/transform_service.py`:
```python
def backtrack_transform_lineage(...): ...   # BFS backtrack over column-derivation edges
def load_edges(...): ...                    # loads a materialized run's edges
def _get_latest_run_id(...): ...            # which run's output to serve
```
The actual expression *reconstruction* (SQL/PySpark parsing, category classification) lives one layer down in `transformation_lineage/parsing/` (`sql_parser.py`, `pyspark_ast_parser.py`) and `transformation_lineage/parsing/graph_builder.py:_classify_transform` — that's where to look if the *expression text or category* is wrong, as opposed to *no lineage found*.

Frontend: `frontend/src/store/transformStore.ts` → `frontend/src/components/transform/TransformPanel.tsx` / `TransformCanvas.tsx` / `TransformNode.tsx` / `TransformEdge.tsx` → `frontend/src/api/transform.ts` (`getTransformTrace`).

**Debug checklist**:
* First confirm the table was ever *built*: `GET /api/transform/freshness` (`transform_service.get_transform_freshness`) — `is_stale`/`last_built` tells you if you're looking at a build problem vs. a trace/render problem.
* If freshness looks fine but the trace is empty/wrong, the bug is in `load_edges` / `backtrack_transform_lineage`, not the parser.
* If the table was never built at all, see capability 3 below (diagnostics) and capability 4 (Lineage Builder) — this is usually a discovery-window or producer-resolution issue, not a parser bug.

---

## 3. Transformation lineage diagnostics

**What breaks visibly**: the diagnose panel gives the wrong reason code, or `unknown` when it shouldn't.

Backend — `backend/main.py`:
```python
@app.get("/api/transform/diagnose")
async def api_transform_diagnose(
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    ...
```

Backend — `backend/transform_service.py`:
```python
def diagnose_missing_lineage(catalog: str, schema: str, table: str) -> TransformDiagnosis: ...
def _latest_extraction_skip_reasons() -> list[str]: ...   # reads lineage_extraction_reports
```
Reason codes are: `no_producer`, `producer_outside_window`, `producer_unresolved`, `unknown` — set inside `diagnose_missing_lineage`. If you're getting `unknown` for a table that should resolve, the bug is almost always that `system.access.column_lineage` itself returned nothing (SP permission or table doesn't have UC lineage yet), not the diagnosis logic.

Frontend: rendered inside `frontend/src/components/transform/TransformPanel.tsx`'s empty-state branch.

**Debug checklist**:
* `DISCOVERY_LOOKBACK_HOURS` (env var, default `8760` = 1 year) — a `producer_outside_window` result with a surprising `days_ago` usually means this env var drifted from what you expect, or `transformation_lineage/config.py`'s `discovery_lookback_hours` is out of sync with it (see CHANGELOG 2.3.0 — these two were previously misaligned).

---

## 4. Lineage Builder (on-demand build jobs)

**What breaks visibly**: "Build" button does nothing, or a build job fails/hangs.

Backend — `backend/main.py`:
```python
@app.post("/api/transform/build")
async def api_transform_build(request: Request, body: BuildJobRequest):
    """Submit a serverless job to build transformation lineage for a table.
    Requires the PIPELINE_NOTEBOOK_PATH to be configured."""

@app.get("/api/transform/status/{run_id}")
async def api_transform_status(run_id: str):
    """Poll the progress of a running transformation lineage build job."""
```

Backend — `backend/build_service.py`:
```python
def submit_build_job(...): ...          # triggers the Lakeflow Job run via Jobs API
def get_build_status(run_id: str) -> BuildJobStatus: ...
def is_build_configured() -> bool: ...  # True only if PIPELINE_NOTEBOOK_PATH env var is set
def _derive_pipeline_notebook_path() -> str: ...
```

Frontend: `frontend/src/components/transform/BuildProgress.tsx` (polls `getBuildStatus`) / `PruningControls.tsx` → `frontend/src/api/transform.ts` (`submitTransformBuild`, `getBuildStatus`, `getBuildConfig`).

**Debug checklist**:
* `GET /api/transform/build-configured` — if `false`, `PIPELINE_NOTEBOOK_PATH` isn't set in `databricks.yml`/app env; the button is correctly disabled, this isn't a bug.
* A submitted-but-stuck build: check the actual Lakeflow Job run in the Databricks Jobs UI via `run_page_url` in the `BuildJobStatus` response — `get_build_status` just polls the Jobs API, it doesn't run anything itself.

---

## 5. Delta Sharing overlay

**What breaks visibly**: shared tables don't show boundary nodes, or `available: false` unexpectedly.

Backend — `backend/main.py`:
```python
@app.get("/api/sharing/overlay")
async def api_sharing_overlay(request: Request, catalog: str = Query(...), schema: str | None = Query(None), ...): ...

@app.get("/api/sharing/overview")
async def api_sharing_overview(request: Request, live: bool = Query(False)):
    """Metastore-wide Delta Sharing inventory for the landing 'Sharing overview' card."""
```

Backend — `backend/lineage_service.py`:
```python
def get_sharing_overlay(catalog: str, schema: str | None, audience: str = "both", ...) -> SharingOverlay: ...
def get_sharing_overview(skip_cache: bool = False) -> dict: ...
def _fetch_share_recipient_map(client) -> dict[str, list[str]]: ...
```

Frontend: `frontend/src/components/graph/SharingNode.tsx` renders the synthetic boundary nodes the overlay produces; matched against graph nodes client-side.

**Debug checklist**:
* `SharingOverlay.available` is `False` when `system.information_schema` (shares/recipients/providers) isn't readable — check `setup.sql` grants, not this code, first.
* This overlay is the same data source `federated_sync.get_federated_sync_status` cross-references (capability 12) — if the *overlay* itself is broken, Federated Sync's `reachable_overlap` count will also silently be wrong; fix this one first.

---

## 6. Serverless cost on entity nodes

**What breaks visibly**: `cost_usd` missing/null on job/pipeline nodes, or a cost that looks stale.

Backend — `backend/lineage_service.py`:
```python
def _refresh_cost_cache(client: WorkspaceClient) -> None: ...   # reads system.billing.usage + list_prices
def _entity_cost(entity_type: str, entity_id: str) -> float | None: ...
def _maybe_refresh_cost_cache(client) -> None: ...              # TTL-gated refresh, not per-request
```
Cost is folded into `EntityNode.cost_usd` inside `_build_graph_from_rows` / `_classify_table_nodes` — it is not a separate endpoint.

Frontend: `frontend/src/components/graph/EntityNode.tsx` renders `cost_usd`; the client-side discount slider (mentioned in README) is purely a display-time multiplier — it never touches this backend value.

**Debug checklist**:
* `None` cost is the documented "classic compute or no data" case, not a bug — only serverless job/pipeline runs have billing rows.
* Cost lags real spend by however stale `system.billing` is upstream (billing has known multi-hour lag on Databricks' side) — a "wrong" cost right after a run is very likely just billing lag, confirm by re-checking a few hours later before assuming a bug in `_refresh_cost_cache`.

---

## 7. Excel export

**What breaks visibly**: `.xlsx` download is empty, missing a sheet, or throws a 500.

Backend — `backend/main.py`:
```python
@app.get("/api/lineage/export")
async def api_export_lineage(request: Request, catalog: str = Query(...), schema: str | None = Query(None), ...): ...
```

Backend — `backend/excel_export.py`:
```python
def build_lineage_workbook(catalog: str, schema: str | None, result, column_edges=None, ...): ...
def _build_lineage_map_sheet(wb, all_nodes, raw_edges, entity_names=None, sheet_name="Lineage Map") -> None: ...
def _add_per_schema_maps(wb, all_nodes, raw_edges, entity_names) -> None: ...
def _layer_nodes(ids, edges): ...   # topological layering for the sheet layout
```

Frontend: `frontend/src/api/client.ts → api.lineageExportUrl(catalog, schema)` — this is a plain `<a href>`/`window.open` download link, not a fetch call, so a broken export usually shows as a browser-rendered FastAPI error page, not a UI error state. Open the URL directly in a new tab to see the real traceback.

**Debug checklist**:
* This function reuses whatever `result` the lineage endpoints already computed — if the graph itself is wrong (capability 1), the export will faithfully reproduce the same wrongness. Rule out capability 1 first.
* `openpyxl`-specific failures (styling, sheet name collisions) are isolated to `_build_lineage_map_sheet`/`_add_per_schema_maps`.

---

## 8. Admin ops dashboard / live mode

**What breaks visibly**: dashboard shows zeros, "Access Denied", or cache invalidation doesn't take effect.

Backend — `backend/main.py`:
```python
@app.get("/api/admin/status")
async def api_admin_status(request: Request):
    """Admin-only utilization dashboard — returns system metrics and cache status."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin: ...

@app.post("/api/cache/invalidate")
async def api_invalidate_cache(request: Request): ...
@app.post("/api/admin/evict-cache")
async def api_admin_evict_cache(request: Request, key: str = Query(...)): ...
```
Metrics themselves (`_request_latencies`, `_request_count`, `_record_latency`) are module-level globals near the top of `backend/main.py`, not in a service module. `lineage_service.get_cache_snapshot()` backs the cache-inventory table.

Frontend: `frontend/src/components/AdminDashboard.tsx`.

**Debug checklist**:
* "Access Denied" is `_get_user_info`'s `is_admin` check (`ADMIN_GROUP_NAME` env var, default `admins`) — verify group membership, not app code, first.
* Metrics reset on every app restart (`_request_latencies = deque(maxlen=1000)` is in-process, unpersisted) — a dashboard that "lost history" after a deploy is expected, not a bug.
* Local dev: `LOCAL_DEV_ADMIN_EMAIL` env var forces `is_admin=True` when there's no `x-forwarded-access-token` header (i.e. running outside the Apps proxy) — set it if the dashboard 403s in local `uvicorn` runs.

---

## 9. Control Panel (feature-flag registry)

**What breaks visibly**: a flag won't toggle, shows the wrong cost/risk, or the panel is empty.

Backend — `backend/main.py`:
```python
@app.get("/api/control-panel/flags")
async def api_control_panel_flags(): ...
@app.post("/api/control-panel/flags/{flag_id}")
async def api_control_panel_set_flag(request: Request, flag_id: str, body: dict):
    """Enable/disable a Control Panel capability. ADMIN ONLY..."""
@app.get("/api/control-panel/access-check/{flag_id}")
async def api_control_panel_access_check(flag_id: str): ...
```

Backend — `backend/feature_flags.py`:
```python
FLAG_DEFINITIONS: list[dict] = [ ... ]        # static metadata: cost/risk/side_effects/access_requirements
_ENV_KILL_SWITCH = { ... }                    # ENABLE_PLAN_CAPTURE / ENABLE_CAPTURED_PLAN_PRECEDENCE / ENABLE_FEDERATED_SYNC
def get_flag_state(flag_id: str) -> bool: ... # persisted DB value AND env kill switch
def list_flags() -> list[dict]: ...
def set_flag_state(flag_id: str, enabled: bool, actor: str) -> dict: ...
def check_access_requirements(flag_id: str) -> list[dict]: ...
```
Persisted in Delta tables `{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.feature_flags` / `.feature_flags_audit`, created lazily by `_ensure_tables()`.

Frontend: `frontend/src/store/featureFlagStore.ts` (state + `useFeatureFlagEnabled` selector used elsewhere in the app) → `frontend/src/components/control-panel/ControlPanel.tsx` (shell) → `ModuleSection.tsx` → `FeatureToggleCard.tsx` (the actual Radix `Switch`, `ACCENT_STYLES` lookup) → `ImpactBadges.tsx` / `AccessRequirementsModal.tsx` → `frontend/src/api/controlPanel.ts`.

**Debug checklist**:
* A flag stuck "off" even after toggling on: check `kill_switched` in the `GET /api/control-panel/flags` response first — an env var (`ENABLE_*=false`) always wins over the DB value, by design (`get_flag_state`'s `AND`).
* Toggle silently does nothing in the UI: check the network tab for a `403` — non-admins get a clean 403 from `api_control_panel_set_flag`, and `FeatureToggleCard.tsx` disables the switch client-side (`toggleDisabled = !isAdmin || busy || flag.kill_switched`) so this should never reach the network for a non-admin; if it does, `isAdmin` in `lineageStore.ts` is stale — check `/api/user-info`.
* Empty panel with no error: `list_flags()` degrades to normal-looking data (all `enabled=False`) even when the Delta table doesn't exist yet — an empty panel is a frontend fetch/render bug, not a backend one, since the backend contract is "never raise, never return empty."

---

## 10. Runtime Plan Capture plugin integration

**What breaks visibly**: status card shows `table_reachable: false` or zero counts after you thought you captured plans; a pipeline notebook's capture call silently does nothing.

Backend read path — `backend/main.py` + `backend/plan_capture_service.py`:
```python
@app.get("/api/control-panel/plan-capture/status")
async def api_plan_capture_status(): ...

def get_plan_capture_status() -> dict:
    """Guarded status probe for the Control Panel card — never raises."""
    enabled = get_flag_state("lineage_tracking.plan_capture")
    ...
def get_captured_expression(catalog, schema, table, column) -> Optional[dict]: ...
```

Backend write path (runs **inside a pipeline notebook**, never in the app process) — `backend/plan_capture/capture.py` + `plan_parser.py`:
```python
DEFAULT_TABLE = f"{os.environ.get('LINEAGE_CATALOG', 'lattice_lineage')}.{os.environ.get('LINEAGE_SCHEMA', 'lineage')}.captured_plans"
def capture(df, target_full_name, ...): ...       # df.explain(mode="extended") → versioned write, non-fatal
def plan_hash(analyzed_plan_text: str) -> str: ...
def parse_plan(analyzed_plan_text: str) -> list[dict]: ...   # per-column {target_column, source_columns, expression, confidence, notes}
```

Frontend: status card lives inside `ControlPanel.tsx` (`planCaptureStatus` from `featureFlagStore.ts`, fetched via `controlPanel.ts → getPlanCaptureStatus`); the captured expression itself surfaces through `api/transform.ts → getCapturedExpression` (see capability 11).

**Debug checklist**:
* Zero counts is *expected* whenever the flag is off (`get_plan_capture_status` short-circuits before any SQL) — confirm the flag is actually on via capability 9 before treating this as a bug.
* If the flag is on but counts are still zero: this is a **write-path** problem, not an app bug — `capture()` only runs inside a pipeline notebook that was manually opted in (see `docs/capabilites.md` for the two-cell opt-in). Check that pipeline's own run logs, not the app's.
* `capture()` is designed to swallow its own errors (never fails the underlying write) — if a pipeline "isn't capturing" with no error anywhere, check the pipeline notebook's stdout for `capture()`'s own non-fatal log line, since a raised exception here would be a contract violation, not the normal failure mode.
* `plan_hash()` strips `#exprId` suffixes so identical logical plans across runs dedupe to one version — if you're seeing more versions than expected, a real expression changed, not a hashing bug.

---

## 11. Captured-Plan Precedence

**What breaks visibly**: the "Runtime-captured" expression never appears in the transformation drill-down even with both flags on.

Backend — `backend/main.py` + `backend/plan_capture_service.py`:
```python
@app.get("/api/transform/captured-expression")
async def api_transform_captured_expression(
    catalog: str = Query(...), schema: str = Query(...), table: str = Query(...), column: str = Query(...),
):
    """Additive enrichment: the Runtime-Captured-Plan expression for one column..."""
```
Depends on *both* `lineage_tracking.plan_capture` and `column_transformation.captured_plan_precedence` being enabled — `plan_capture_service.get_captured_expression` now short-circuits unless both effective flag states are `True`.

Frontend: `frontend/src/store/transformStore.ts` (`loadCapturedExpression`) → `frontend/src/components/transform/TransformPanel.tsx` (best-effort Runtime-captured card, shown in both the `needs_build` and `ready` panel states when available) → `frontend/src/api/transform.ts`.

**Debug checklist**:
* Confirm *both* `lineage_tracking.plan_capture` AND `column_transformation.captured_plan_precedence` are on — the backend returns `null` unless both effective flag states are enabled.
* If Control Panel counts are non-zero but no Runtime-captured card appears, check `transformStore.loadCapturedExpression` and `TransformPanel.tsx` before blaming plan parsing — the frontend now fetches this best-effort alongside freshness/trace loads.
* Returns `null` (not an error) whenever there's no captured plan for that exact `target_full_name`, or the column name isn't present in the parsed plan — check `plan_capture_service.get_captured_expression`'s early-return branches before assuming the parser is broken.

---

## 12. Federated Sync

**What breaks visibly**: a registered peer never shows as "known" on a shared boundary node; peer registration 403s or silently fails.

Backend — `backend/main.py` + `backend/federated_sync.py`:
```python
@app.get("/api/control-panel/federated/status")
async def api_federated_sync_status(): ...
@app.get("/api/control-panel/federated/peers")
async def api_federated_list_peers(): ...
@app.post("/api/control-panel/federated/peers")
async def api_federated_register_peer(request: Request, body: dict):
    """Register a known peer workspace/metastore for the Federated Sync overlay.
    ADMIN ONLY..."""

def list_federated_peers() -> list[dict]: ...
def register_federated_peer(peer_alias, share_name, direction, actor, notes="") -> dict: ...
def get_federated_sync_status() -> dict:
    """... cross-references registered peers against the existing sharing overview's
    known share/foreign catalog names ..."""
```
Persisted in `{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.federated_peers`, created lazily by `_ensure_table()`.

Frontend: status card in `ControlPanel.tsx` (`federatedSyncStatus`) via `controlPanel.ts → getFederatedSyncStatus/listFederatedPeers/registerFederatedPeer`. There is currently no dedicated peer-management UI beyond the status card counts — registration is exercised via the API directly or a future admin form (see `docs/architecture.md` Known Gaps).

**Debug checklist**:
* `reachable_overlap` stuck at 0 despite peers registered: `get_federated_sync_status` matches on **exact `share_name` string equality** between your registry and `get_sharing_overview()`'s `shared_out`/`foreign_catalogs` — a mismatched alias/casing in the `share_name` you registered vs. the real Delta Share name is the #1 cause, not a bug in the cross-reference logic itself.
* This is explicitly a v1 registry, **not** live cross-workspace sync — if you're expecting this module to make an actual API call to a peer workspace, that functionality doesn't exist yet (see `docs/architecture.md` §5 Known Gaps and the module docstring in `federated_sync.py`). Don't spend time debugging a "missing" live-sync feature that was never built.
* 403 on registration: same admin gate as capability 9/10 — check `is_admin` via `/api/user-info` before assuming the registration endpoint is broken.

---

## 13. LLM-assisted PySpark → SQL enrichment

**What breaks visibly**: a PySpark-derived transformation shows no SQL-normalized expression, or the displayed SQL form looks stale/wrong even though the build completed.

Backend — `transformation_lineage/pipeline.py`:
```python
# ── Phase 8: Expression Enrichment (best-effort, non-blocking) ─────
with _phase("expression enrichment (ai_query LLM)"):
    try:
        enrich_stats = enrich_pyspark_expressions(
            spark,
            pipeline_run_id=pipeline_run_id,
            endpoints_table=tables["edge_endpoints"],
            cache_table=tables["pyspark_to_sql_cache"],
        )
```

Backend — `transformation_lineage/sublineage/expression_enricher.py`:
```python
"""Translate PySpark column expressions to SQL via Databricks `ai_query`."""
DEFAULT_MODEL = "databricks-meta-llama-3-3-70b-instruct"

def enrich_pyspark_expressions(...):
    """Translate PySpark `expr` -> SQL `expr_sql` for the given pipeline run.
    Returns counts: pending, translated, backfilled."""
```

Important scope note: this is **not** an LLM that invents lineage edges. It is a best-effort enrichment stage that translates already-discovered PySpark expressions into SQL text and backfills `expr_sql` for existing transformation-edge endpoints.

Frontend: surfaced indirectly through `frontend/src/components/transform/TransformPanel.tsx` after `frontend/src/api/transform.ts → getTransformTrace`; there is no standalone app endpoint for this phase.

**Debug checklist**:
* If the lineage graph/column dependency is missing entirely, this stage is irrelevant — it only enriches expression text after the transformation build already found the edge.
* Failures here are intentionally non-blocking (`logger.warning("expression enrichment failed (continuing): %s", e)`) — a successful build with missing SQL-normalized expressions can still be an LLM-enrichment failure.
* The cache table is `lineage_pyspark_to_sql_cache`; stale/wrong repeats are usually a cache-content issue, not a route/UI issue.
* This only runs for rows where `expr_lang = 'pyspark'` and `expr_sql IS NULL`; SQL-native expressions do not go through the LLM path at all.

---

## 14. Landing explorer & browse flows

**What breaks visibly**: the landing page is empty, Browse/Schema lineage/Catalog lineage cards dead-end, or catalog/schema pickers look incomplete.

Backend — `backend/main.py`:
```python
@app.get("/api/tables")
async def api_list_tables():
    """Return all tables across all catalogs (cached). Frontend filters client-side."""

@app.get("/api/catalogs")
async def api_list_catalogs(): ...

@app.get("/api/schemas")
async def api_list_schemas(catalog: str = Query(...)):
    catalog = _validate_identifier(catalog, "catalog")
```

Backend — `backend/lineage_service.py`:
```python
def list_catalogs() -> list[str]: ...
def list_all_tables() -> list[dict]: ...
def list_schemas(catalog: str) -> list[str]: ...
```

Frontend: `frontend/src/components/landing/Landing.tsx`, `components/landing/LineagePicker.tsx`, `components/browse/CatalogListView.tsx`, `components/browse/SchemaListView.tsx`, plus `hooks/useRecents.ts`.

**Debug checklist**:
* Empty landing explorer almost always traces back to `GET /api/tables` returning nothing because accessible catalogs/schemas were filtered out or `BROWSE` is missing — rule out permissions before debugging React.
* Schema/Catalog lineage picker flows rely on the same browse APIs plus the scope routes in capability 15; if the list renders but clicking through fails, the bug is probably routing/state, not listing.
* The landing Sharing overview card also depends on `GET /api/sharing/overview`; if only that card is empty while tables load fine, debug capability 5, not browse listing.

---

## 15. Deep-link routing & scoped lineage entrypoints

**What breaks visibly**: `?table=...` or `?view=schemaLineage` opens the wrong screen, browser history behaves strangely, or scoped lineage loads the wrong granularity.

Frontend routing — `frontend/src/hooks/useRouter.ts`:
```typescript
export type Route =
  | { view: "landing" }
  | { view: "catalogs" }
  | { view: "schemas"; catalog: string }
  | { view: "tables"; catalog: string; schema: string }
  | { view: "lineage"; table: string }
  | { view: "schemaLineage"; catalog: string; schema: string }
  | { view: "catalogLineage"; catalog: string }
  | { view: "admin" }
  | { view: "controlPanel" };
```

Backend entrypoints — `backend/main.py`:
```python
@app.get("/api/lineage")
async def api_get_lineage(request: Request, catalog: str = Query(...), schema: str | None = Query(None), live: bool = Query(False)):
    # schema is optional: omitting it builds catalog-wide lineage across all schemas
    ...

@app.get("/api/lineage/trace")
async def api_lineage_trace(request: Request, table: str = Query(...), live: bool = Query(False)):
    """End-to-end cross-catalog lineage trace from a single seed table."""
```

Frontend callers: `frontend/src/App.tsx` coordinates route → page selection; `components/landing/LineagePicker.tsx`, `components/browse/CatalogListView.tsx`, and `components/browse/SchemaListView.tsx` trigger `goLineage`, `goSchemaLineage`, and `goCatalogLineage`.

**Debug checklist**:
* `?table=catalog.schema.table` should land on the table-focused trace path (`/api/lineage/trace`), while `?view=schemaLineage` / `?view=catalogLineage` go through `/api/lineage` with schema optional. If the wrong graph shape appears, confirm the route parser first.
* History/back-button bugs almost always live in `navigate()` / `routeToSearch()` / `parseRoute()` in `useRouter.ts`, not in the lineage services.
* If scoped lineage works from in-app buttons but fails from pasted URLs, the bug is route parsing/encoding, not backend lineage logic.

---

## 16. In-graph exploration controls

**What breaks visibly**: view-mode toggles do nothing, depth filtering is wrong, Cmd/Ctrl+K search fails to center a node, or reset/fitView behaves badly on large graphs.

Frontend state — `frontend/src/store/lineageStore.ts` tracks:
```typescript
lineageView: "full" | "table" | "pipeline"
lineageDepth: number
columnLineageEnabled: boolean
expandedNodes: Set<string>
selectedNode: ...
selectedColumn: ...
searchOpen: boolean
globalSearchOpen: boolean
previewOpen: boolean
```

Frontend rendering: `frontend/src/components/graph/LineageCanvas.tsx`, `components/layout/Toolbar.tsx`, `components/ui/SearchDialog.tsx`, `components/graph/TableNode.tsx`, `components/graph/EntityNode.tsx`.

Important scope note: this capability family is mostly **frontend behavior over already-fetched lineage data**. The bugs here are usually state/layout/filtering bugs, not bad system-table reads.

**Debug checklist**:
* `lineageDepth` only limits rendered table hops; entity nodes are treated as transparent connectors. A "depth is wrong" complaint can be expected behavior.
* Column lineage is intentionally disabled in pipeline-only view and in catalog-wide scope (`columnsDisabled = lineageView === "pipeline" || (isScopeLineage && scope === "catalog")`) — if the toggle disappears there, that is by design.
* Large-graph centering issues usually live in the `fitView` retry path inside `LineageCanvas.tsx`, not in ELK layout input generation.
* Search bugs are usually graph-state bugs (`SearchDialog`/selected node/zoom-to-node), not backend search/listing bugs; the in-graph search only searches the **currently rendered graph**, not all workspace tables.

---

## Cross-cutting debug entry points

These aren't tied to one capability but are the fastest first checks for *any* symptom above:

| Check | What it tells you |
|---|---|
| `GET /api/diagnostics` | Whether the SP can actually reach `system.access`, `system.billing`, `information_schema`, and catalog BROWSE. Rules out "it's a permissions problem" in one call. |
| `GET /api/user-info` | `isAdmin` — the gate behind every admin-only action (cache invalidate, flag toggles, peer registration). |
| `GET /api/admin/status` (admin only) | Live request-latency percentiles, memory RSS, cache inventory — use this before assuming a slow capability is a logic bug vs. a load/cache-sizing issue. |
| Structured JSON logs (`_JsonLogFormatter` in `main.py`) | Every `logger.error`/`logger.warning` in the modules above emits one JSON line with `level`/`logger`/`msg` — grep app logs by `"logger": "backend.<module>"` to isolate a capability's own log lines. |
| Env vars in `databricks.yml` | `LINEAGE_CATALOG`/`LINEAGE_SCHEMA` (where every app-owned table in this doc lives), `DATABRICKS_WAREHOUSE_ID` (every `_execute_sql` in every service module needs this), `ADMIN_GROUP_NAME`, `ENABLE_PLAN_CAPTURE`/`ENABLE_CAPTURED_PLAN_PRECEDENCE`/`ENABLE_FEDERATED_SYNC` (kill switches). A misconfigured/missing value here explains more "capability doesn't work" reports than an actual code bug. |

---

*Updated for the fully combined app in `/Workspace/Users/ayaskanta.ratha@databricks.com/lineage_app/lineage-explorer-transforms_combined`. Added coverage for browse flows, scoped routing, in-graph controls, and the LLM-backed PySpark-expression enrichment stage, alongside [docs/architecture.md](architecture.md), [docs/capabilites.md](capabilites.md), and [docs/testing_plan_for_Combined_App.md](testing_plan_for_Combined_App.md). See [CHANGELOG.md](../CHANGELOG.md) for the full release history.*
