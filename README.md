<p align="center">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="96" height="96">
<polygon points="50,4 96,27 50,50 4,27" fill="#FF5520"/>
<polygon points="4,27 50,50 50,96 4,73" fill="#BB2700"/>
<polygon points="96,27 50,50 50,96 96,73" fill="#DD3700"/>
</svg>
</p>

<h1 align="center">BrickRoute</h1>

<p align="center">
  <strong>Unified lineage visualization with transformation drill-down</strong> — end-to-end table &amp; column lineage, expression-level transformation graphs, pipeline/job visibility, serverless cost, and Delta Sharing, across every catalog in your metastore. One command to deploy; zero access to your row data.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/React-18-blue" alt="React"/>
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white" alt="TypeScript"/>
  <img src="https://img.shields.io/badge/Databricks_Apps-FF3621?style=flat&logo=databricks&logoColor=white" alt="Databricks"/>
  <img src="https://img.shields.io/badge/ELK.js-layout-orange" alt="ELK.js"/>
  <img src="https://img.shields.io/badge/version-2.4.0-6366F1" alt="Version"/>
</p>

---

Unity Catalog captures lineage from every SQL operation — but reading it means querying system tables by hand. BrickRoute turns those system tables into an interactive graph, deployed as a Databricks App and shared across your whole workspace.

## Features

- **Expression-level transformation lineage — the part that's genuinely unique.** Unity Catalog records that "column A depends on column B." BrickRoute goes a layer deeper and reconstructs the **actual SQL/PySpark expression** that produced each column — `cast`, `sum`, `concat`, `CASE`, window functions, CTE chains — tagged with a transform category, by genuinely **parsing the producing code**. Coverage spans every producer type: notebook/Python/SQL-file jobs, Python- and SQL-defined DLT pipelines, view/materialized-view/streaming-table definitions, and ad-hoc query history. Click any column → see *how* it was derived, traced upstream. *(See [docs/DESIGN.md](docs/DESIGN.md).)*
- **Transformation lineage diagnostics** — when a build materialises nothing, the app explains *why* instead of a generic "not generated yet": no producing pipeline found (source/ingested table), producer last ran outside the discovery window (with the exact age), or producer source couldn't be read (with the missing permission). Each reason includes a suggested fix. (`GET /api/transform/diagnose`)
- **Select anything → full end-to-end lineage.** Pick any table (search or browse) and it auto-traces the complete lineage cone — every upstream source back to every downstream target — **across all catalogs and schemas**, with the mediating pipeline/job nodes. No buttons to press; the only control is the view mode.
- **Column-level lineage** traced from real `system.access.column_lineage` edges — no name-matching heuristics, zero false positives.
- **Delta Sharing, always in the picture.** Shared-in sources and shared-out targets show up as part of lineage (with provider/recipient boundary nodes). The trace stops honestly at the metastore boundary — we can't read the other account.
- **Serverless cost on pipeline/job nodes** — 30-day list price from `system.billing`, with a client-side discount control.
- **Three view modes** — Tables, Pipelines, or Full.
- **Scales to thousands of users on one query** — request coalescing + a memory-bounded LRU/TTL cache mean the warehouse is barely touched.
- **Admin live mode + built-in ops dashboard** (P50/P95/P99 latency, memory, cache inventory, transformation-lineage invalidate controls).
- **Excel export** — styled multi-sheet `.xlsx` of the lineage data (`GET /api/lineage/export`).
- **Deep-link embeddable** — `?table=catalog.schema.table` from any dashboard or tool.
- **Metadata-only access** — the app reads `BROWSE` + system tables, never your table data. Transformation-lineage builds write only to one dedicated app-owned schema — never to your data catalogs.
- **Control Panel (v2.4.0)** — an admin-gated toggle center for three opt-in capabilities, all OFF by default: **Runtime Plan Capture** (captures Spark's exact per-column expression at execution time — the only path for transformation logic the static parser can't read from source), **Captured-Plan Precedence** (surfaces that captured expression in the transformation drill-down), and **Federated Sync** (a curated registry of known peer workspaces layered on the existing Delta Sharing overlay). Every user can see what each capability does and its access requirements; only workspace admins can flip a toggle. See [docs/capabilites.md](docs/capabilites.md).

## Quick start

Deploying to your own workspace is a short ordered checklist — not every step is automated, so follow them in order. Run `/api/diagnostics` at the end to confirm.

**0. Account-admin prerequisites** (do these once, before deploying):
- **Enable system tables** (Account console → Settings → System tables): `system.access` (lineage — *required*, the #1 "empty app" cause if missing), `system.billing` (cost), `system.information_schema` (Delta Sharing).
- A **SQL warehouse** (serverless or pro) and **Unity Catalog**. Databricks CLI **v0.239+**.

**1. Deploy** (creates the app + its service principal):

    databricks auth login --profile <profile>
    databricks bundle deploy -t dev --profile <profile> --var warehouse_id=<warehouse-id>
    databricks bundle run brickroute -t dev --profile <profile>

> The app name defaults to `brickroute-dev` (dev) / `brickroute` (prod). In a shared workspace, override it to avoid collisions: `--var app_name=<your-name>`.

**2. Grant the app's service principal** (as a **metastore admin**). The SP only exists after step 1. Easiest path — the helper resolves the SP and applies the grants:

    ./grant_app_access.sh --profile <profile> --warehouse <warehouse-id> --catalogs "catalog_a catalog_b"

Or do it by hand: fill the `:APP_SP` / `:CATALOG` placeholders in **[`setup.sql`](setup.sql)** and run it. End-to-end traces span catalogs, so grant `BROWSE` on **every** catalog you want visible.

**3. (Optional) Live mode** — enable App on-behalf-of OAuth + scopes `iam.current-user:read`, `iam.access-control:read`, and set `--var admin_group_name=<your-admin-group>` if it isn't `admins`.

**4. Verify** — open `https://<app-url>/api/diagnostics`. It reports exactly which prerequisites the SP can reach (warehouse, `system.access`, `system.billing`, `information_schema`, catalog BROWSE), so a misconfigured deploy surfaces a clear reason instead of an empty graph.

**5. (Optional) Enable an opt-in capability** — open the app, click the header menu → **Control Panel**, and as a workspace admin toggle on Runtime Plan Capture, Captured-Plan Precedence, and/or Federated Sync. All three are OFF by default and take no action on their own until you also complete the capability-specific setup described in [docs/capabilites.md](docs/capabilites.md) (e.g. adding a capture call to a pipeline notebook, or registering a federated peer).

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
| [Capability → code map](docs/capability_code_map.md) | Every capability (new and pre-existing) mapped to its exact backend route, service function, frontend component, and data table — for fast debugging |
| [Testing plan](docs/testing_plan_for_Combined_App.md) | Unit tests, frontend checks, and a manual QA checklist for the additions above |

`docs/ARCHITECTURE.md` (the pre-existing, all-caps reference covering the core lineage/transformation engine) has been updated for v2.4.0 as well — its new §1.2 links out to the docs above rather than duplicating them.

## Tech stack

FastAPI + Uvicorn (single process, 64-thread pool) · Databricks SDK + DBSQL over UC system tables · React + TypeScript + React Flow + ELK.js (layout in a Web Worker) · deployed via Databricks Asset Bundles.

## License

See [License](docs/REFERENCE.md#license).
