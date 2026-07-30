<p align="center">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="96" height="96">
<polygon points="50,82 85,65 50,48 15,65" fill="#8B1A00"/>
<polygon points="15,65 50,82 50,90 15,73" fill="#6B1400"/>
<polygon points="85,65 50,82 50,90 85,73" fill="#7A1800"/>
<polygon points="50,68 85,51 50,34 15,51" fill="#BB2700"/>
<polygon points="15,51 50,68 50,76 15,59" fill="#991F00"/>
<polygon points="85,51 50,68 50,76 85,59" fill="#AA2300"/>
<polygon points="50,54 85,37 50,20 15,37" fill="#DD3700"/>
<polygon points="15,37 50,54 50,62 15,45" fill="#BB2700"/>
<polygon points="85,37 50,54 50,62 85,45" fill="#CC3000"/>
<polygon points="50,40 85,23 50,6 15,23" fill="#FF5520"/>
<polygon points="15,23 50,40 50,48 15,31" fill="#DD3700"/>
<polygon points="85,23 50,40 50,48 85,31" fill="#EE4410"/>
</svg>
</p>

<h1 align="center"><span style="color:#1a1a2e">Brick</span><span style="color:#FF3621">Trace</span></h1>
<p align="center"><em>End-to-End Data Lineage for Databricks</em></p>

<p align="center">
  <strong>Unified lineage visualization with transformation drill-down</strong> — end-to-end table &amp; column lineage, expression-level transformation graphs, pipeline/job visibility, serverless cost, and Delta Sharing, across every catalog in your metastore. One command to deploy; zero access to your rows data.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/React-18-blue" alt="React"/>
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white" alt="TypeScript"/>
  <img src="https://img.shields.io/badge/Databricks_Apps-FF3621?style=flat&logo=databricks&logoColor=white" alt="Databricks"/>
  <img src="https://img.shields.io/badge/ELK.js-layout-orange" alt="ELK.js"/>
  <img src="https://img.shields.io/badge/version-2.6.0-6366F1" alt="Version"/>
</p>

---

Unity Catalog captures lineage from every SQL operation — but reading it means querying system tables by hand. BrickTrace turns those system tables into an interactive graph, deployed as a Databricks App and shared across your whole workspace.

## Features

This is the user-visible capability inventory. `docs/capability_code_map.md` is a debugging index of top-level capability families and code anchors, not an exhaustive list of every UX surface, route, or always-on workflow.

- **Landing explorer + browse flows.** Browse catalogs, schemas, and tables; open schema-wide or catalog-wide lineage from picker flows; use recents and global search to jump back into prior objects quickly.
- **Deep-link routing for every major surface.** `?table=catalog.schema.table` opens table lineage directly; `?view=schemaLineage` and `?view=catalogLineage` open broader scopes; `?admin=true` opens the ops dashboard; `?controlPanel=true` opens the Control Panel.
- **Select anything → full end-to-end lineage.** Pick any table (search or browse) and it auto-traces the complete lineage cone — every upstream source back to every downstream target — **across all catalogs and schemas**, with the mediating pipeline/job nodes. No buttons to press; the only control is the view mode.
- **Three view modes + depth control.** Switch between **Tables**, **Pipelines**, or **Full**, then cap the rendered graph to N table hops when you want a local slice instead of the whole cone.
- **In-graph exploration tools.** Cmd/Ctrl+K graph search, drag-to-rearrange, reset/fit-view behavior, hover tooltips, orphan highlighting, and large-graph layout retries are built in.
- **Column-level lineage** traced from real `system.access.column_lineage` edges — no name-matching heuristics, zero false positives.
- **Cross-schema / cross-catalog node enrichment.** External-in-scope tables render as distinct nodes with full metadata and remain expandable/clickable for lineage tracing rather than collapsing into anonymous placeholders.
- **Expression-level transformation lineage — the part that's genuinely unique.** Unity Catalog records that "column A depends on column B." BrickTrace goes a layer deeper and reconstructs the **actual SQL/PySpark expression** that produced each column — `cast`, `sum`, `concat`, `CASE`, window functions, CTE chains — tagged with a transform category, by genuinely **parsing the producing code**. Coverage spans every producer type: notebook/Python/SQL-file jobs, Python- and SQL-defined DLT pipelines, view/materialized-view/streaming-table definitions, and ad-hoc query history. Click any column → see *how* it was derived, traced upstream. *(See [docs/DESIGN.md](docs/DESIGN.md).)*
- **Transformation freshness + on-demand Lineage Builder.** The transform panel checks whether a table's transformation lineage exists or is stale, can submit a build/regenerate job, and polls build progress when serverless build execution is configured. The notebook path is resolved lazily: env var → App source discovery (via `apps.get`) → fail-closed. `/Users` and `/Shared` prefixes are auto-normalized to `/Workspace/...`.
- **Transformation lineage diagnostics** — when a build materialises nothing, the app explains *why* instead of a generic "not generated yet": no producing pipeline found (source/ingested table), producer last ran outside the discovery window (with the exact age), or producer source couldn't be read (with the missing permission). Each reason includes a suggested fix. (`GET /api/transform/diagnose`)
- **Delta Sharing, always in the picture.** Shared-in sources and shared-out targets show up as part of lineage (with provider/recipient boundary nodes). The trace stops honestly at the metastore boundary — we can't read the other account.
- **Serverless cost on pipeline/job nodes** — 30-day list price from `system.billing`, with a client-side discount control.
- **Admin live mode + built-in ops dashboard** (P50/P95/P99 latency, memory, cache inventory, manual eviction, request metrics, and transformation-lineage invalidate controls).
- **Excel export + preview.** Export lineage as a styled multi-sheet `.xlsx` (`GET /api/lineage/export`) and preview the rows in-app before download.
- **Scales to thousands of users on one query** — request coalescing + a memory-bounded LRU/TTL cache mean the warehouse is barely touched.
- **Metadata-only access** — the app reads `BROWSE` + system tables, never your table data. Transformation-lineage builds write only to one dedicated app-owned schema — never to your data catalogs.
- **Control Panel (v2.4.0)** — an admin-gated toggle center for opt-in capabilities, all OFF by default: **Runtime Plan Capture**, **Captured-Plan Precedence**, and **Federated Sync**. Every user can see what each capability does and its access requirements; only workspace admins can flip a toggle. See [docs/capabilites.md](docs/capabilites.md).
- **Governance & classification (v2.5.0)** — ownership + column sensitivity from `information_schema` + app-owned rules; downstream sensitivity propagation. (`GET /api/governance`)
- **Impact analysis / blast radius (v2.5.0)** — BFS walk over `system.access.table_lineage` enumerating all downstream tables with owner and sensitivity enrichment. (`GET /api/impact`)
- **Observability / run health (v2.5.0)** — per-entity success-rate and failure counts from `system.lakeflow.job_run_timeline` / `pipeline_update_timeline`. (`GET /api/observability`)
- **Access & security lineage (v2.5.0)** — declared UC grants joined with empirical access from `system.access.audit`; surfaces dormant grants. (`GET /api/access`)
- **AI/ML lineage (v2.5.0)** — serving endpoint inventory, daily usage, and model→training-data lineage. (`GET /api/ml/endpoints`, `/api/ml/models-for-table`)
- **Search & discovery (v2.5.0)** — full-text UC asset search, PII/sensitive-column finder, orphan-table detector. (`GET /api/search`, `/api/discover/sensitive`, `/api/discover/orphans`)
- **Data quality rules (v2.5.0)** — per-column DQ expectations (NOT_NULL, UNIQUE, RANGE, REGEX, CUSTOM) persisted in an app-owned Delta table, separate from pipeline expectations. (`GET|POST|DELETE /api/dq-rules`)
- **Diagnostics suite (v2.5.0)** — root-cause tracing from a failing column (`POST /api/diagnostics/root-cause`), SCD/CDC spec viewer (`GET /api/diagnostics/scd`), schema-change / breaking-change detector (`GET /api/diagnostics/schema-changes`), column profiling overlay (`GET /api/diagnostics/profile`), live billing for Control Panel cards (`GET /api/diagnostics/billing/{flag_id}`), and live federated peer trust handshakes + sync triggering (`GET|POST /api/diagnostics/federated/...`).
- **LLM producer source analysis (v2.5.0)** — fetches producer source code and calls the workspace LLM to infer per-column transformations; results versioned in an app-owned `producer_analysis` store. (`POST /api/analyze-producer`)
- **Automated pipeline capture installer (v2.5.0)** — admin action that injects the `%pip install` + `capture()` cells into a pipeline notebook via the Workspace API (idempotent). (`POST /api/pipeline/install-capture`)
- **BFS captured-plan precedence override (v2.5.0)** — when both plan-capture flags are enabled, runtime-captured expressions now *override* (not just augment) the BFS walk in `backtrack_transform_lineage`. (no new route — `transform_service._get_captured_expression_for_node`)
- **Table Lineage workspace (v2.5.5)** — a dedicated per-table analysis workspace opened from the **Table Lineage** home tile (or `?view=tableLineage`): a left catalog tree, the cross-catalog lineage graph, a summary bar, and six **draggable** capability panels — Impact (with clickable consumer deep-links), Root Cause (health-based upstream trace), Governance (classification-rule management), Access & security, ML Models, and LLM Transform (model dropdown + versioning + version compare). See [docs/capability_code_map.md](docs/capability_code_map.md) Part F.
- **Light / dark theme (v2.5.5)** — theme toggle (top-right) backed by CSS-variable color tokens; the whole UI flips from one class on `<html>`. Preference persists across sessions.
- **Redesigned home + new logo (v2.5.5)** — sidebar-shell landing (nav, workspace selector, user profile, notifications, Recent Activity) and a new BrickTrace logo across the app.
- **Cached capability panels (v2.6.0)** — Impact, Root Cause, Governance, and Access results are cached per table in Delta and served instantly on reopen (~25× faster than a cold scan). Each panel shows a "cached Xh ago / may be stale" badge and a refresh icon; admins can evict per entry, per table, or the whole cache from the ops dashboard. (`/api/admin/capability-cache`)
- **Per-node run health check (v2.6.0)** — every job/pipeline node in the graph has a health icon opening a popover: Healthy/Degraded/Failing verdict, success rate, duration trend, total cost with a per-run spike flag, and the last 5 runs with **real per-run cost** and deep links. (`GET /api/observability/runs`)
- **Multi-producer transformation comparison (v2.6.0)** — when a table is written by two or more producers, the Column Transformation panel shows a side-by-side per-column matrix that flags where producers compute the same column with different logic — catching silent consistency hazards. (`POST /api/column-transformations/compare-producers`)
- **Actionable access-denied guidance (v2.6.0)** — when the LLM path can't read a producer's source, the panel names the exact resource and app service principal to grant, instead of a generic error. Pipeline source-fetch now also covers modern bundle/DLT pipelines whose source is `glob`- or `file`-based (not just declared notebooks).

## Quick start

Deploying to your own workspace is a short ordered checklist — not every step is automated, so follow them in order. Run `/api/diagnostics` at the end to confirm.

### Prerequisites

**Local tooling** (on the machine you deploy from):
- **Node.js 18+ and npm** — required to build the React frontend (`tsc && vite build`). Verify with `node -v` / `npm -v`. Without this you'll hit `sh: tsc: command not found`.
- **Python 3.10+** — only needed for local backend runs and the `make` helper targets (`status`/`diagnostics` shell out to `python3`).
- **Databricks CLI v0.239+** — `databricks -v`. This is the new (Go) CLI that ships Asset Bundles, not the legacy `databricks-cli` pip package.

**Account-admin prerequisites** (do these once, before deploying):
- **Enable system tables** (Account console → Settings → System tables): `system.access` (lineage — *required*, the #1 "empty app" cause if missing), `system.billing` (cost), `system.information_schema` (Delta Sharing), `system.lakeflow` (observability), `system.serving` (ML lineage).
- A **SQL warehouse** (serverless or pro) and **Unity Catalog**.

### Install & deploy

**1. Clone and install the frontend dependencies.** Databricks Apps serve a *prebuilt* `frontend/dist`, so the frontend must be built before (or during) deploy — and the build tools (`tsc`, `vite`) only exist after an install.

    git clone <repo-url> && cd lineage-explorer-transforms
    cd frontend && npm ci && cd ..     # `npm ci` installs the exact locked versions
    npm --prefix frontend run build     # produces frontend/dist (the Makefile's `build` target does this too)

> The backend deps (`requirements.txt`) are installed by the Databricks Apps runtime at deploy time — you don't need a local venv unless you're running the backend locally.

**2. Deploy** (creates the app + its service principal):

    databricks auth login --profile <profile>
    databricks bundle deploy -t dev --profile <profile> \
      --var warehouse_id=<warehouse-id> \
      --var lineage_catalog=<catalog-you-can-write> \
      --var lineage_schema=<schema> \
      --var captured_plans_table=<catalog>.<schema>.captured_plans \
      --var captured_cdc_table=<catalog>.<schema>.captured_cdc_specs
    databricks bundle run bricktrace -t dev --profile <profile> \
      --var warehouse_id=<warehouse-id> \
      --var lineage_catalog=<catalog-you-can-write> \
      --var lineage_schema=<schema> \
      --var captured_plans_table=<catalog>.<schema>.captured_plans \
      --var captured_cdc_table=<catalog>.<schema>.captured_cdc_specs

> **Which `--var`s are required.** Only `warehouse_id` has no default. But `lineage_catalog`/`lineage_schema` (where the app writes its own tables) default to `lattice_lineage.lineage`, and `captured_plans_table`/`captured_cdc_table` default under that same catalog — so **on any workspace that can't create `lattice_lineage`** (e.g. a metastore with no default storage root), leaving them unset causes `TABLE_OR_VIEW_NOT_FOUND` / catalog-creation failures. Point them at a catalog + schema the app's service principal can write to.
>
> **Pass the same `--var`s to `bundle run` too**, not just `deploy`. The App's runtime env is (re)applied on `bundle run`, so any var you drop there resets that env value — the usual cause of "No SQL warehouse available / `DATABRICKS_WAREHOUSE_ID` not set" after a restart.
>
> The app name defaults to `bricktrace-dev` (dev) / `bricktrace` (prod). In a shared workspace, override it to avoid collisions: `--var app_name=<your-name>`.
>
> **Shortcut — use the `Makefile`.** `make redeploy` runs build → deploy → run in one step and bakes in all of the above `--var` overrides so a config change or restart never drops one. Override defaults on the command line, e.g. `make redeploy PROFILE=<profile> WAREHOUSE_ID=<id> LINEAGE_CATALOG=<cat> LINEAGE_SCHEMA=<schema>`. Run `make help` to list targets.

**3. Grant the app's service principal** (as a **metastore admin**). The SP only exists after step 2. Easiest path — the helper resolves the SP and applies the grants:

    chmod +x grant_app_access.sh    # first time only — the script may land non-executable after clone
    ./grant_app_access.sh --profile <profile> --warehouse <warehouse-id> --catalogs "catalog_a catalog_b"

> If you still see `permission denied`, run it through the shell directly: `bash grant_app_access.sh --profile <profile> ...`. And retype the arguments by hand rather than pasting — smart/curly quotes (the `“ ”` a doc or chat app auto-inserts) are **not** valid shell quotes and will corrupt the catalog names. Use plain straight quotes.

Or do it by hand: fill the `:APP_SP` / `:CATALOG` placeholders in **[`setup.sql`](setup.sql)** and run it. End-to-end traces span catalogs, so grant `BROWSE` on **every** catalog you want visible.

**4. (Optional) Live mode** — enable App on-behalf-of OAuth + scopes `iam.current-user:read`, `iam.access-control:read`, and set `--var admin_group_name=<your-admin-group>` if it isn't `admins`.

**5. Verify** — open `https://<app-url>/api/diagnostics` (or run `make diagnostics`). It reports exactly which prerequisites the SP can reach (warehouse, `system.access`, `system.billing`, `information_schema`, catalog BROWSE), so a misconfigured deploy surfaces a clear reason instead of an empty graph.

**6. (Optional) Enable opt-in capabilities** — open the app, click the header menu → **Control Panel**, and as a workspace admin toggle on Runtime Plan Capture, Captured-Plan Precedence, and/or Federated Sync. All are OFF by default. See [docs/capabilites.md](docs/capabilites.md) for setup steps. The v2.5.0 API capabilities (governance, impact, observability, access, ML, discovery, DQ, diagnostics) are always-on — no toggle required.

## Documentation

The full reference lives in **[docs/REFERENCE.md](docs/REFERENCE.md)**:

| Topic | |
|---|---|
| [Transformation lineage (the unique part)](docs/REFERENCE.md#transformation-lineage-coverage) | Expression-level column derivation; producer-type coverage; design in [docs/DESIGN.md](docs/DESIGN.md) |
| [Deploy & permissions](docs/REFERENCE.md#permissions-reference) | Prerequisites, SPN grants, DABs deploy, post-deploy setup |
| [Features & view modes](docs/REFERENCE.md#features) | Graph, canvas, node/edge types, depth control |
| [User identity & live mode](docs/REFERENCE.md#user-identity--live-mode) | On-behalf-of auth, admin gating |
| [Caching & concurrency](docs/REFERENCE.md#caching--concurrency) | Cache keys, single-flight, memory sizing |
| [Cost optimization](docs/REFERENCE.md#cost-optimization) | Warehouse sizing, cost-cache behavior, billing lag |
| [Architecture & scaling](docs/REFERENCE.md#architecture) | Single-process model, scaling beyond one process |
| [Configuration & API](docs/REFERENCE.md#configuration) | Env vars, endpoints |
| [Troubleshooting](docs/REFERENCE.md#troubleshooting) | Common symptoms & fixes |

**New in v2.4.0** — Control Panel, Runtime Plan Capture, and Federated Sync:

| Topic | |
|---|---|
| [Architecture of the new modules](docs/architecture.md) | Data flow, module boundaries, and an explicit Known Gaps list |
| [Capabilities catalog](docs/capabilites.md) | What each Control Panel toggle does, access requirements, pipeline opt-in steps |
| [Capability → code map](docs/capability_code_map.md) | Top-level capability families mapped to their backend routes, service functions, frontend entries, and key data tables — for fast debugging |
| [Testing plan](docs/testing_plan_for_Combined_App.md) | Unit tests, frontend checks, and a manual QA checklist for the additions above |

**New in v2.5.0** — Governance, impact, observability, access, ML lineage, discovery, DQ, LLM analysis, diagnostics suite:

| Topic | |
|---|---|
| [Capability → code map (caps 17-36)](docs/capability_code_map.md) | Routes, service functions, system-table sources, frontend entries, and debug checklists for all 20 new capabilities |
| [Capabilities catalog](docs/capabilites.md) | Updated: live billing badges (cap 31), live federated trust (cap 32), BFS override (cap 30), pipeline installer (cap 29) |
| `/api/diagnostics` router | Caps 29-36 consolidated under `backend/routes/diagnostics.py` (`prefix="/api/diagnostics"`) |
| `/api/governance`, `/api/impact`, `/api/observability`, `/api/access`, `/api/ml`, `/api/search`, `/api/dq-rules` | Dedicated `APIRouter` modules for caps 17-23, all registered in `backend/main.py` lines 75-85 |
| `/api/lineage/column-path`, `/api/lineage/entities`, `/api/lineage/freshness`, `/api/analyze-producer` | Caps 24-28 via `backend/routes/lineage.py` (`lineage_ext_router` + `analyze_router`) |
| `/api/pipeline/install-capture` | Cap 29 via `backend/routes/pipeline_installer.py` |

`docs/ARCHITECTURE.md` (the pre-existing, all-caps reference covering the core lineage/transformation engine) has been updated for v2.4.0 as well — its new §1.2 links out to the docs above rather than duplicating them.

**New in v2.5.5** — Table Lineage workspace, light/dark theme, redesigned home:

| Topic | |
|---|---|
| [Capability → code map — Part F](docs/capability_code_map.md) | The `table-lineage/` workspace, per-panel file map, theme/logo shell, and the backend deltas (Impact consumers, Root Cause trace, SP-friendly Access, live ML derivation, LLM versioning). |
| [CHANGELOG — 2.5.5](CHANGELOG.md) | Full added/changed list + known gaps. |
| `?view=tableLineage` | The workspace route (`hooks/useRouter.ts` `goTableLineage`), opened from the Table Lineage home tile. |

**New in v2.6.0** — cached capability panels, per-node run health, multi-producer comparison:

| Topic | |
|---|---|
| [CHANGELOG — 2.6.0](CHANGELOG.md) | Full added/changed/fixed list. |
| Per-table capability cache | `backend/server/capability_cache.py` (`serve_or_compute`) + `/api/admin/capability-cache` eviction; `CacheHeader` refresh badge in the 4 panels. |
| Run health check | `observability.get_recent_runs()` + `GET /api/observability/runs`; `HealthPopover` on JOB/PIPELINE nodes in `EntityNode.tsx`. |
| Multi-producer comparison | `producer_source.compare_producers()` + `POST /api/column-transformations/compare-producers`; `ProducerCompareMatrix` in `ColumnTransformationPanel.tsx`. |
| Product one-pager | [`docs/BrickTrace_Product_Overview.pdf`](docs/BrickTrace_Product_Overview.pdf) — sales-oriented capabilities overview. |

## Tech stack

FastAPI + Uvicorn (single process, 64-thread pool) · Databricks SDK + DBSQL over UC system tables · React + TypeScript + React Flow + ELK.js (layout in a Web Worker) · deployed via Databricks Asset Bundles.

## License

See [License](docs/REFERENCE.md#license).
