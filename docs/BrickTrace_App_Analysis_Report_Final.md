# BrickTrace — Comprehensive App Analysis Report

**Product:** BrickTrace  
**Code reference (latest):** `lineage-explorer-transforms-feature-Lineage_App_Combined 5`  
**Perspective:** Databricks Apps deployment  
**Method:** Static code review of `backend/`, `frontend/`, `transformation_lineage/`, `docs/`, `databricks.yml`, `setup.sql`, `grant_app_access.sh`  
**Original audit date:** July 21, 2026  
**Report updated:** July 22, 2026 (against Combined **5**)  
**Prior trees:** Combined → 2 → 3 → 4 → **5**  
**Backend / frontend version:** `2.5.4` / `2.5.4`  
**Shipped SPA:** `frontend/dist/assets/index-CrAkK-Bv.js` (~229 KB)

> **Verdict:** Combined 5 is **close to rollout-ready for core lineage + the five product panels** (DQ, Glossary, Notifications, Export, Root Cause). Menu navigation for those panels is fixed.  
> **What can still break UX or confuse users:** BI Consumers / Streaming Topology are in the **menu and router but not mounted in `App.tsx`** (click → landing page). Backend now attaches `graph_warnings`, but the **frontend never reads them**. Deploy/ops misconfiguration (warehouse, grants, system tables, notebook path) can still make the App look empty. Multi-user UC isolation (C9) remains an architectural limit.

---

## What Combined 5 fixed vs Combined 4

| Item | Combined 4 | Combined **5** |
|------|------------|----------------|
| `goDQ` / glossary / notif / export / RCA helpers | Missing / broken | ✅ **Exported** |
| `routeToSearch` for those views | Incomplete | ✅ **Complete** |
| Header menu for DQ / Glossary / Notif / Export / RCA | Missing | ✅ **Present** |
| Dist rebuilt | `index-BfSeI32Z.js` | **`index-CrAkK-Bv.js`** |
| BI / Streaming panels | Absent | Components + menu + router exist; **not rendered in `App.tsx`** |
| Lineage `graph_warnings` (C2–C5) | Helpers only | ✅ Attached on lineage + trace APIs in `main.py` |
| FE consumption of `graph_warnings` | N/A | ❌ **Still unused** |
| C14 soft-warm `get_recent_entries` | Fixed in 4 | ✅ Still present |

---

## Remaining issues that can block the App or create user issues

### P0 — User-facing breakage (fix now)

#### R1 — BI Consumers / Streaming Topology menu is a dead end
| | |
|--|--|
| **Severity** | High (UX bug) |
| **Symptom** | Menu shows “BI Consumers” / “Streaming Topology”; URL becomes `?view=biConsumers` / `?view=streaming`; UI falls through to **Landing** |
| **Cause** | `BiConsumersPanel` / `StreamingTopologyPanel` imported in `App.tsx` but **no `route.view === …` render branches**; tree-shaken out of dist (`/api/lineage/bi-consumers` = 0 in bundle) |
| **Impact** | Looks like “frontend not working” for those items |
| **Fix** | Mount both panels in `App.tsx` (same pattern as glossary/dq); rebuild dist |

#### R2 — API ↔ panel contract mismatch (will bite once R1 is fixed)
| Panel | API returns | Panel expects |
|-------|-------------|---------------|
| **BI Consumers** | `bi_tool`, `query_count`, `distinct_users`, `last_accessed` | `source_table`, `entity_type`, `entity_id`, `entity_name`, `last_query_time`, `query_count` |
| **Streaming** | `streaming_tables` rows (`table_catalog/schema/name`), `streaming_edges` | Nodes with `table_fqn` / `is_streaming`; edges from **`data.edges`** (API key is **`streaming_edges`**) |

| **Severity** | Medium–High once mounted |
| **Impact** | Empty tables / blank UI even when API succeeds |
| **Fix** | Align panel mapping to live API shapes (or adapt API) |

### P1 — Silent / incomplete product behavior

#### R3 — `graph_warnings` not shown in UI
- Backend sets `result.graph_warnings` on lineage + trace (`main.py` + `models.py`).
- Frontend has **zero** references to `graph_warnings`.
- **Impact:** Partial catalog access, foreign boundaries, truncation, lookback staleness (C2–C5) stay invisible → users trust incomplete graphs.
- **Fix:** Store warnings in `lineageStore`; banner in Toolbar/canvas.

#### R4 — Truncation helper not applied
- `build_graph_warnings` may *flag* large graphs; `apply_graph_truncation` is still **uncalled**.
- **Impact:** Large estates can still OOM / slow the App; warnings alone don’t bound memory.

#### R5 — No FE banner for system-table / grant health (C1)
- `/health` returns `system_health`; startup logs C1.
- App UI does not surface “system tables unavailable / missing grants.”
- **Impact:** Empty lineage looks like a product bug.

### P2 — Deploy / ops blockers (can make App empty or build fail)

These are **not new code bugs**, but they **will block** a working Apps rollout:

| ID | Risk | What happens |
|----|------|----------------|
| **D1** | Deploy without `--var warehouse_id` | App incomplete / no SQL |
| **D2** | Deployer lacks warehouse `CAN_MANAGE` | Cannot attach warehouse resource |
| **D3** | System tables not enabled at account | Empty lineage regardless of app code |
| **D4** | Skip full `setup.sql` / SP grants | Empty graph, BI query failures (`system.query`), broken capture schema |
| **D5** | `pipeline_notebook_path` unset | Transform **build** unavailable (fail-closed — good, but feature missing) |
| **D6** | Ship UI without rebuilding `dist` | Users miss FE fixes (R1 will need a rebuild) |

### P3 — Architectural / known limits (won’t “crash” App; will create trust issues)

| ID | Issue | Status |
|----|-------|--------|
| **C9** | All SQL as App SP — no per-user UC OBO | ❌ Open platform limit; gate via Apps ACL + tight SP BROWSE |
| **Federated Sync** | Scaffold, not live cross-workspace sync | PARTIAL (docs honest) |
| **Observability dashboard** | API exists; dedicated UI thin | PARTIAL |
| **apply_graph_truncation / foreign / partial helpers** | Partially used via warnings metadata only | Incomplete |

---

## Audit scoreboard (Combined 5)

### Bugs from original audit

| ID | Title | Status |
|----|-------|--------|
| A1 SQL injection | ✅ CLOSED |
| A2 Admin gates | ✅ CLOSED |
| A3 Captured-plan BFS | ✅ CLOSED |
| **A4** Orphan panels / dist / nav | ✅ **CLOSED for DQ/Glossary/Notif/Export/RCA**; ⚠️ **BI/Streaming residual (R1)** |
| A5 Version drift | ✅ CLOSED |
| A6–A8, A9–A15 | ✅ CLOSED |

### Edge cases

| ID | Status in Combined 5 |
|----|----------------------|
| C6, C8, C10, C12, C14, C15 | ✅ CLOSED |
| C1 | ⚠️ API/startup only — no FE banner (R5) |
| C2–C5 | ⚠️ Metadata on API (`graph_warnings`) — **no FE** (R3); truncation not applied (R4) |
| C7, C11, C16 | ⚠️ IMPROVED (startup / APIs) |
| C9, C13 | ❌ / static docs |

### Frontend product path

| Surface | Menu | Router | Mounted in App | In dist |
|---------|------|--------|----------------|---------|
| Control Panel | ✅ | ✅ | ✅ | ✅ |
| DQ / Glossary / Notifications / Export / RCA | ✅ | ✅ | ✅ | ✅ |
| **BI Consumers** | ✅ | ✅ | ❌ **R1** | Menu only |
| **Streaming Topology** | ✅ | ✅ | ❌ **R1** | Menu only |
| Core lineage / transform | ✅ | ✅ | ✅ | ✅ |

---

## Can the App run today?

**Yes, for the main path**, if deploy prerequisites are met:

1. `bundle deploy` with valid `warehouse_id`
2. Full grants / `setup.sql` (+ system tables enabled)
3. Users open App → browse → lineage → transform / Control Panel / glossary / DQ / etc. via menu

**What will still create “issues” reports:**

1. Clicking **BI Consumers** or **Streaming Topology** → appears broken (R1).  
2. After fixing mount without fixing contracts → empty BI/streaming tables (R2).  
3. Incomplete graphs with no warning banners (R3/R5).  
4. Misconfigured warehouse/grants/system tables → empty App (D1–D4).  
5. Shared App SP visibility surprises (C9).

---

## Recommended fix order (blockers first)

1. **Mount** `BiConsumersPanel` / `StreamingTopologyPanel` in `App.tsx` for `biConsumers` / `streaming`.  
2. **Fix response mapping** to match `/api/lineage/bi-consumers` and `/api/lineage/streaming-topology`.  
3. **`npm run build`** and redeploy `frontend/dist`.  
4. Wire `graph_warnings` (+ optional `/health` summary) into Toolbar banners.  
5. Optionally call `apply_graph_truncation` before returning large graphs.  
6. Keep ops checklist: warehouse var, grants, system tables, notebook path, Apps ACL.

---

## How the App runs (unchanged)

1. User opens App URL → optional `x-forwarded-access-token`.  
2. FastAPI serves API + **`frontend/dist`**.  
3. SQL/Jobs as **App service principal** (not OBO).  
4. Startup: perf patches + edge-case guards (C1/C7/C14).  
5. Optional plan capture from customer pipelines → app Delta.

---

## Appendix — Key Combined 5 files

| Topic | Path |
|-------|------|
| Missing BI/Streaming mounts | `frontend/src/App.tsx` |
| Menu + go helpers (OK) | `HeaderMenu.tsx`, `useRouter.ts` |
| Orphan BI/Streaming components | `BiConsumersPanel.tsx`, `StreamingTopologyPanel.tsx` |
| Graph warnings attach | `backend/main.py`, `backend/models.py` |
| Warnings helpers | `backend/edge_case_guards.py` |
| Shipped SPA | `frontend/dist/assets/index-CrAkK-Bv.js` |
| Deploy vars | `databricks.yml` (`warehouse_id`, `pipeline_notebook_path`) |

---

## Remediation timeline

| Tree | Outcome |
|------|---------|
| Combined / 2 | Backend security; Control Panel in dist |
| Combined 3 | Edge-case module; FE version align; scorecard honesty |
| Combined 4 | Five panels mounted + dist; C14; health/SCD/capture APIs |
| **Combined 5** | Full menu/nav for five panels; graph_warnings on API; BI/Streaming **menu without mount** |

---

*Static audit of Combined 5. Not a live penetration test or workspace `/api/diagnostics` run.*
