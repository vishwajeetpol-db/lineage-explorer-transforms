# BrickTrace — App Readiness Report

**Product:** BrickTrace  
**Version:** `2.5.4`  
**Date:** July 22, 2026
---
## Verdict

**The App is ready to function.**

Final Build complete product path: backend security remediations, core lineage/transform UI, Control Panel, DQ, Glossary, Notifications, Export, Root Cause, BI Consumers, Streaming Topology, graph-warning banners, and system-health checks — all present in **source and shipped `frontend/dist`**.

There is **no remaining code blocker** that prevents the App from starting or serving the main experience.

What can still break a working deploy is **missing platform / ops prerequisites** below.

---

## Prerequisites (if missing, the App can break or look empty)

| # | Prerequisite | What breaks if missing |
|---|--------------|------------------------|
| 1 | Deploy with a valid SQL warehouse: `--var warehouse_id=<id>` | No SQL execution → lineage and most APIs fail |
| 2 | Deploying user has warehouse **`CAN_MANAGE`** | Bundle cannot attach the sql-warehouse resource |
| 3 | Unity Catalog **system tables** enabled at the account | Empty lineage regardless of app code |
| 4 | Run **`setup.sql`** (or equivalent grants): App SP needs `SELECT` on `system.access` (and `system.query` for BI), plus catalog **`BROWSE`**, and app schema rights on `lattice_lineage` (or configured lineage catalog) | Empty graph, BI consumer errors, capture/flags schema failures |
| 5 | Set **`pipeline_notebook_path`** if transform **builds** are required | Transform build unavailable (fails closed by design) |
| 6 | Restrict **who can open the App** (Apps ACL) and scope App SP **`BROWSE`** tightly | Not a crash — but every opener sees whatever the App SP can see (no per-user OBO) |

---

## Ship checklist

1. Frontend is built  (dist already rebuilt — no extra frontend build needed unless you change `frontend/src`).
2. Confirm prerequisites **1-4** (and **5** if builds matter).
3. Apply prerequisite **6** before wide rollout.
4. Smoke-test: Home → browse catalogs → open a table lineage → Control Panel → DQ / Glossary → BI Consumers → Streaming Topology.
---
## Optional polish (does not block App function)

- Streaming edge labels may show a generic `"stream"` text (`relationship` vs API `entity_type`).
- Some nested graph-warning fields may render awkwardly in the banner until formatted.
- Federated Sync and Observability remain intentionally thinner than core lineage.
---

