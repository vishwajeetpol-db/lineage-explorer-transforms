# Testing Plan — Combined App (2.4.0 additions)

Scope: the Control Panel, Runtime Plan Capture, and Federated Sync
capabilities added in 2.4.0 (see [architecture.md](architecture.md) and
[capabilites.md](capabilites.md)). Pre-existing lineage/transform/sharing test
coverage already lives in `backend/tests/` — this plan adds to it, it does not
replace it.

## 1. Unit tests to add (`backend/tests/`)

### `test_feature_flags.py` (new)
* `get_flag_state()` returns `False` for an unregistered flag id — never
  raises `KeyError`.
* `get_flag_state()` returns `False` when the env kill switch is set to
  `"false"` regardless of the persisted DB value (mock `_ENV_KILL_SWITCH`).
* `list_flags()` degrades to `enabled=False` for every flag (not an exception)
  when `_execute_sql` raises — e.g. warehouse unreachable, table doesn't exist
  yet. This is the single most important test: a Control Panel that 500s
  because the feature-flag table hasn't been created yet would be worse than
  the flags themselves being off.
* `set_flag_state()` raises `ValueError` for an unknown `flag_id` (caller in
  `main.py` maps this to a 404).
* `check_access_requirements()` returns `satisfied=None` (not a crash) when
  the underlying `SHOW GRANTS`/`SELECT` probe raises for reasons other than a
  genuine permission denial (e.g. a transient warehouse timeout) — this
  currently returns `False` rather than `None` in that case (see §4 below,
  logged as a known follow-up, not a blocking bug).

### `test_plan_capture_service.py` (new)
* `get_plan_capture_status()` returns the all-zero/disabled shape when the
  flag is off — asserts the function short-circuits **before** attempting any
  SQL, i.e. mock `_execute_sql` to raise and confirm it's never called.
* `get_plan_capture_status()` degrades `table_reachable=False` (not an
  exception) when `captured_plans` doesn't exist yet.
* `get_captured_expression()` returns `None` when the flag is off, when no row
  matches `target_full_name`, and when the column name isn't present in the
  parsed plan — three distinct None-producing paths, each needs its own case.
* `get_captured_expression()` correctly threads a captured `analyzed_plan`
  string through `plan_parser.parse_plan()` and matches on `target_column` —
  use a fixture plan text (there are several suitable ones in the
  `lineage-plan-capture` source project's own test fixtures/FINDINGS.md
  examples; reuse rather than reinvent).

### `test_plan_capture_capture.py` (new — tests the vendored write path)
* `plan_hash()` is stable across two plan strings that differ only in
  `#exprId` suffixes (the whole point of stripping them) but changes when the
  actual expression text changes.
* `capture()` returns `{"ok": False, ...}` (never raises) when called with no
  active SparkSession — this is the exact non-fatal contract the module
  promises pipeline authors.
* `capture()` returns `{"ok": True, "status": "unchanged"}` on a second call
  with an identical plan (dedup path) — requires a local/mocked Spark session;
  if CI has no Spark available, mark `skipif` and rely on the vendored
  source project's own existing test coverage for this path, cross-referenced
  in the module docstring.

### `test_federated_sync.py` (new)
* `list_federated_peers()` returns `[]` when the flag is off — asserts no SQL
  call is attempted (same pattern as feature flags).
* `get_federated_sync_status()` degrades to `reachable_overlap=0` (not an
  exception) when `get_sharing_overview()` raises or returns an unexpected
  shape (not a dict, or missing keys) — this function must tolerate the
  sharing overview's return type changing without warning, since it's a
  cross-module dependency, not a stable public contract.
* `register_federated_peer()` rejects/normalizes an invalid `direction` value
  to `"both"` rather than writing garbage into the registry.

### `test_control_panel_routes.py` (new, alongside existing `test_api.py`)
* `GET /api/control-panel/flags` returns 200 with all flags `enabled=False`
  on a completely fresh app (no feature_flags table yet) — the "cold start"
  case that must never 500.
* `POST /api/control-panel/flags/{flag_id}` returns 403 for a non-admin caller
  (mirror the existing `test_auth.py` pattern for `/api/cache/invalidate`).
* `POST /api/control-panel/flags/{flag_id}` returns 404 for an unregistered
  flag id, for both admin and non-admin callers (403 should NOT take priority
  over 404 — actually verify which the current implementation returns first,
  since `main.py` checks admin before calling `set_flag_state`; document
  whichever behavior is correct rather than assuming).
* `GET /api/transform/captured-expression` returns `{"captured": null}` (200,
  not an error) for a column with no captured plan — this is a contract test
  that the frontend's "fall back to static parse" logic can rely on.

## 1b. Unit tests added for v2.6.0 (`tests/`)

New capabilities (cached panels, run health, multi-producer comparison) each have a test module. All mock the SDK/`_execute_sql` — no live workspace needed. Run with `pytest tests/ -m "not integration"`.

### `test_capability_cache.py`
* `CapabilityCache` singleton; `get`/`set`/`evict`/`evict_table`/`evict_all`/`inventory` all return safe defaults on SQL error (cache is optional, non-fatal).
* `get()` returns the payload + `stale` flag regardless of age; treats NULL `value_json` as a miss; `set()` rejects oversized payloads and escapes single quotes.
* `serve_or_compute()`: cache hit skips `compute` and marks `from_cache=True`; miss computes + stores; `refresh=True` bypasses the read; non-dict payloads pass through uncached.

### `test_run_health.py`
* `get_recent_runs()`: verdict/success-rate math, per-run cost mapping + total + spike flag (>2× median), duration-trend (latest vs prior avg), JOB vs PIPELINE dispatch + deep-link shape, unsupported entity type, missing-billing → null costs (not error), timeline error → empty (not raise).
* `GET /api/observability/runs`: bad entity_type/injection → 400, out-of-range limit → 422, valid request returns the payload (cache bypassed via patched `CapabilityCache`).

### `test_producer_source.py`
* `_is_access_error` / `_FetchDiag` classification (access_denied vs entity_missing, path dedup).
* `_fetch_pipeline_source` handles `notebook` / `file` / `glob` library shapes from the raw REST spec (glob base dir stripped of wildcards then walked).
* `analyze_producer` returns `reason_code` (`access_denied` with `denied_paths`+`app_service_principal`, or `no_source`).
* `compare_producers`: divergent-column flagging, present/absent divergence, and that it resolves each producer via `analyze_producer` (per-entity) — **not** `resolve_column_transformations` (table-level) which would mask divergence.
* `POST /api/column-transformations/compare-producers`: requires ≥2 producers, rejects injection, returns the matrix.

### `test_capability_cache_routes.py`
* Impact/Access/Root Cause routes attach a `_cache` meta block; a cache hit skips recompute; `refresh=true` bypasses the read and recomputes; a miss computes then caches.
* `GET /api/admin/capability-cache` + `POST .../evict` are admin-gated (403 for non-admin), validate `scope` (entry needs table+tab; invalid scope → 400), and return the evicted count.

## 1c. Full backend-route coverage sweep (v2.6.0)

A coverage audit found ~41% of endpoints had tests; the following files were added so **every** router has at least validation + admin-gate + happy-path coverage (service layer mocked, no live workspace):

| File | Router(s) covered |
|---|---|
| `test_routes_core_lineage.py` | core lineage in `main.py` — `/api/lineage`, `/lineage/trace`, `/columns`, `/column-lineage`, `/schema-column-lineage`, `/tables`, `/catalogs`, `/schemas`, `/sharing/*`, `/entity-name`, `/lineage/export` |
| `test_routes_column_transformations.py` | `/api/analyze-producer/*`, `/api/column-transformations(+versions/compare)`, `/api/lineage/column-path\|entities\|freshness` |
| `test_routes_root_cause.py` | `/api/root-cause/analyze\|trace\|upstream-path\|run-failures` |
| `test_routes_coverage_extras.py` | `/api/access(+schema)`, `/api/observability/producers`, ML extensions, `/api/governance/config` DELETE, `/api/dq-rules/propagation` |
| `test_routes_glossary.py` | `/api/glossary/*` (terms, domains, KPIs, links, overlays) |
| `test_routes_snapshots.py` | `/api/snapshots/*` (capture/list/get/diff/delete) |
| `test_routes_scalability.py` | `/api/scalability/*` (graph pagination, cache stats/invalidate, health) |
| `test_routes_openlineage.py` | `/api/export\|import/openlineage`, `/api/openlineage/producer/*` |
| `test_routes_pipeline_installer.py` | `/api/pipeline/install-capture(+preview)` (admin gate) |
| `test_routes_diagnostics.py` | `/api/diagnostics/*` (root-cause, scd, schema-changes, profile, federated) |

Run the whole backend suite with `pytest tests/ -m "not integration"`.

## 2. Frontend checks

* `useFeatureFlagStore` — `updateFlagEnabled` only mutates the targeted flag's
  `enabled` field, leaves every other flag object referentially unchanged
  (React re-render correctness for a list of toggle cards).
* `ControlPanel.tsx` optimistic-toggle rollback: mock `setFeatureFlag` to
  reject, assert the toggle visually reverts to its prior state and an error
  message renders — this is the one interactive path in the new UI that can
  silently drift out of sync with the backend if untested.
* `FeatureToggleCard.tsx` — the Radix `Switch.Root` is `disabled` when
  `isAdmin=false`, `busy=true`, or `flag.kill_switched=true`; each of the
  three should be tested independently (a card can be kill-switched for an
  admin, and that must still disable the switch).

## 3. Manual QA checklist (pre-release)

1. Fresh deploy, no flags toggled yet: open Control Panel as a non-admin —
   confirm all three cards render, are read-only, and show `enabled: false`.
   Confirm no console errors even though `feature_flags` table doesn't exist
   in the catalog yet.
2. As an admin, toggle **Runtime Plan Capture** on. Confirm
   `GET /api/control-panel/plan-capture/status` now returns
   `enabled: true, table_reachable: false` (table still doesn't exist —
   nothing has captured yet) rather than an error.
3. Add the two-cell capture snippet from
   [capabilites.md](capabilites.md#lineage-tracking--runtime-plan-capture) to
   a real pipeline notebook, run it once. Confirm `captured_plans` now has a
   row and the status card's counts update.
4. Toggle **Captured-Plan Precedence** on. Open the transformation
   drill-down for the column just captured — confirm a "Runtime-captured"
   badge/expression appears alongside the static-parse result, and that
   turning the flag back off makes it disappear on next open (client calls
   `useFeatureFlagEnabled` to gate the fetch).
5. Toggle **Runtime Plan Capture** off while Captured-Plan Precedence is
   still on — confirm the Control Panel visually reflects the broken
   dependency (at minimum, the dependent card should not claim data is fresh;
   full dependency-cascade UX was not implemented in 2.4.0, so verify this is
   at least not silently misleading, and file a follow-up if it is).
6. Register a Federated Sync peer via the API (see
   [capabilites.md](capabilites.md#federated-sync--cross-workspace-lineage-sync)),
   toggle the flag on, confirm the status card's `registered_peers` count
   updates and `reachable_overlap` is non-negative-sane (0 if the share name
   doesn't match anything real yet).
7. Set `ENABLE_PLAN_CAPTURE=false` in the app's environment (redeploy or local
   run) and confirm the Control Panel shows the card as **kill-switched**,
   the toggle is disabled, and `get_flag_state()` returns `False` even though
   the DB flag may still say `true` — this is the one behavior that must work
   even if the SQL warehouse is completely down.
8. Confirm `GET /health` still returns `{"status": "ok", "version": "2.4.0"}`
   and the version now matches `CHANGELOG.md`'s latest entry (this was
   previously out of sync — see CHANGELOG.md `[2.4.0]`).

## 4. Follow-ups identified while writing this plan (not blocking 2.4.0)

* `check_access_requirements()` collapses "genuinely denied" and "transient
  probe failure" into the same `satisfied=False` result — a flaky warehouse
  could make an access check look like a real permissions problem. Splitting
  these (e.g. `satisfied=None` on network/timeout errors, `False` only on a
  clean permission-denied response) is a reasonable v1.1 improvement.
* No automated test yet exercises the Runtime Plan Capture *write path*
  end-to-end against a real Delta table (only the read path and the pure
  functions `plan_hash`/`analyzed_plan` are practically unit-testable without
  a Spark cluster). Consider a lightweight integration test job that runs
  `capture()` against a scratch table on serverless compute as part of CI.
* Federated Sync's `reachable_overlap` computation assumes
  `get_sharing_overview()`'s dict shape is stable; if that function's return
  type ever changes to a Pydantic model instead of a dict, `federated_sync.py`
  will silently report `known_shares=0` instead of erroring — worth revisiting
  once `SharingOverview` (already a Pydantic model in `models.py`) is
  consistently returned by `get_sharing_overview()` rather than a raw dict.
