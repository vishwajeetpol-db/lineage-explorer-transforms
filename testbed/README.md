# BrickTrace test bed

Synthetic fixtures that exercise every producer type and capability panel the app
supports, in one droppable schema plus a handful of jobs and pipelines.

```bash
# 1. Authenticate as YOURSELF (not the deploy SP — it cannot create anything)
databricks auth login --host https://fevm-ws-us-e2-vish-aws-databricks-1.cloud.databricks.com --profile vish-user

# 2. See exactly what would happen, change nothing
./testbed/build_testbed.sh --profile vish-user --tier 2 --dry-run

# 3. Build it
./testbed/build_testbed.sh --profile vish-user --tier 1        # producer coverage
./testbed/build_testbed.sh --profile vish-user --tier 2        # + ops/quality fixtures

# Remove everything (schema, jobs, pipelines, uploaded sources)
./testbed/build_testbed.sh --profile vish-user --cleanup
```

Defaults: catalog `ws_us_e2_vish_aws_databricks_1_catalog`, schema `bt_testbed`,
warehouse `11c2f090646163af`, app `bricktrace-dev`. Override with `--catalog`,
`--schema`, `--warehouse`, `--app`. `--skip-runs` creates everything without
triggering a single run (zero compute).

**Two things to know before you judge a result.**

1. **Lineage comes from runs, not definitions.** Nothing appears in the app until
   each producer has actually run, and `system.access.column_lineage` lags a
   finished run by roughly 5–60 minutes. An empty graph in the first minutes is
   expected, not a bug.
2. **Which fixtures test *transformation* lineage, and which don't.** The tables
   built by the SQL script run through the warehouse as ad-hoc statements, so they
   land in `system.access.column_lineage` with `entity_type = NULL`. Those are
   great for table/column lineage, Impact, Governance and the DQ/PII panels — but
   a *transformation* build on one will honestly report `no_producing_query`,
   because query history is identity-scoped and the app SP cannot read yours (the
   same documented limitation as the 20 `stress_test` tables). The fixtures built
   for transformation lineage are the ones with a real tracked producer:
   `v_order_summary` (a VIEW — resolved from its definition, no query history
   involved), `sqltask_order_agg`, `pyfile_customer_norm`, `multi_metrics`,
   `framework_output`, `xcat_trips_daily`, and the four DLT tables.
3. **The builder grants the app's service principal `USE SCHEMA` + `SELECT` on the
   new schema.** A new schema inherits nothing, and catalog `BROWSE` alone is not
   enough for the `SHOW CREATE TABLE` that view / MV / streaming-table resolution
   depends on. If you build into a different schema by hand, grant it yourself.

## What this adds on top of what the workspace already had

Already present, and still worth testing against: the `bronze`/`silver`/`gold`
medallion (notebook-job producers), `lineage_lab` (Python DLT, a materialized
view, a streaming table), and the 20-layer `stress_test` chain (deep lineage —
and a documented limitation, since those tables were made by ad-hoc SQL whose
query history the app SP cannot read).

The gaps this test bed fills: **no plain VIEW anywhere**, **no `sql_task`
producer**, **no `spark_python_task` producer**, **no SQL-defined DLT**, **no
`apply_changes`/SCD2 spec**, **no table written by two producers with divergent
logic**, **no config-table-driven ETL**, **no cross-catalog producer**, **no
deliberately failing or slow job**, and **no PII / DQ-edge-case / schema-evolution
fixtures**.

## Tier 1 — producer-type coverage

| Fixture | Producer mechanism | Exercises | Expected result |
|---|---|---|---|
| `v_order_summary` | plain **VIEW** | Definition-based resolution (`SHOW CREATE TABLE`) — no lineage lag | Transformation lineage resolves immediately, without any build discovery. `order_net_amount` = `round(sum(oi.net_amount), 2)` [AGGREGATION], `status_norm` = `upper(o.status)` [STRING_FN] |
| `sqltask_order_agg` | **`sql_task`** job `bt-sqltask-order-agg` (a workspace `.sql` FILE) | The resolver's `sql_task` branch — present in code, never cleanly tested | `channel_revenue` = `round(sum(quantity*unit_price*(1-discount_pct)), 2)`, `channel_code` = `concat('CH-', channel)`. Also covers CTE resolution: the columns resolve **through** the `paid` CTE to `silver` base columns |
| `pyfile_customer_norm` | **`spark_python_task`** job `bt-pyfile-customer-norm` (plain `.py` FILE, not a notebook) | PySpark AST parser on a non-notebook artifact | `full_name_upper` = `upper(...)`, `email_domain` = `split(email,'@')[1]`, `tenure_days` = `datediff(...)` |
| `sqldlt_orders_clean` (ST), `sqldlt_channel_revenue` (MV) | **SQL-defined DLT** pipeline `bt-sql-dlt-channel` | SQL DLT path + `STREAM(...)` unwrapping + `LIVE.*` references | `status_norm` = `upper(status)`, `channel_code` = `concat('CH-',channel)`, `revenue` = `round(sum(net_amount),2)`. The `STREAM(...)` source must resolve to the plain table underneath |
| `scd2_customers` | **DLT AUTO CDC** (`apply_changes`) pipeline `bt-cdc-scd2-customers` | SCD/CDC spec viewer, `captured_cdc_specs` | `GET /api/diagnostics/scd` reports `scd_type=2`, `keys=[customer_id]`, `sequence_by=change_ts`; the table carries `__START_AT`/`__END_AT`, two rows per customer with the first closed out |
| `multi_metrics` | **two notebook jobs**, `bt-multi-producer-a` and `-b`, writing the same table | Multi-producer comparison matrix | The **Producers** tab shows both producers and flags `net_revenue` as divergent: A applies the discount (`qty*price*(1-discount)`), B does not (`qty*price`). `avg_unit_price` also differs (plain vs weighted average) |
| `framework_output` + `etl_config` | **config-table-driven** notebook `bt-framework-driver` | Deep framework analysis (the agentic second pass) | Normal analysis finds **no** column logic — the notebook contains none. Deep analysis detects the config mechanism, reads `etl_config`, and derives all 7 column rules (`revenue_usd`, `margin_pct`, `order_month`, `size_bucket`, `is_cancelled`, …) |
| `xcat_trips_daily` | notebook `bt-xcat-trips` reading **`samples.nyctaxi.trips`** | Cross-catalog lineage, external-in-scope node rendering | `samples.nyctaxi.trips` renders as an upstream node in a *different catalog* with full metadata and stays clickable; column lineage crosses the boundary (`trip_miles` ← `trip_distance`) |
| `hub_orders_wide` → 6 `spoke_*` tables | SQL fan-out | Impact / blast radius breadth, and the business-view "data only" mesh fix | Impact lists **6** downstream tables. In business view → Data only, you should see 6 distinct `source → target` pairs, **not** an everything-to-everything mesh |

## Tier 2 — ops, quality, governance

| Fixture | Exercises | Expected result |
|---|---|---|
| `pii_customers` (`email`, `phone`, `ssn`, `dob`, `credit_card`, `ip_address`) → `pii_contact_export` | Sensitive-column finder, governance classification, downstream sensitivity propagation | `GET /api/discover/sensitive` flags all six columns; the Governance panel propagates sensitivity to `pii_contact_export`. Values are synthetic — no real personal data |
| `dq_edge_cases` | DQ rule types **and** the UC-legal-name regression | A rule on `` `Order Date` `` (space) and `` `order-id` `` (hyphen) **saves and evaluates** rather than 400-ing or landing as `invalid`. `nullable_note` fails NOT_NULL, `dup_key` fails UNIQUE, `out_of_range_pct` fails RANGE (0–100), `messy_code` fails REGEX (`^[A-Z]{2}-[0-9]{4}$`) |
| `evolving_orders` | Schema-change / breaking-change detector | Two additive changes (`currency`, `settled_at`) reported non-breaking, the `amount` INT→BIGINT widening reported as a change, and the `legacy_ref` **drop** reported as breaking |
| `orphan_snapshot` | Orphan detector | Listed by `GET /api/discover/orphans`; renders with `lineage_status = orphan` |
| `bt-health-flaky` (3 runs: pass, fail, pass) | Run-health verdicts | Node health popover reads **Degraded**, success rate 67%, last-5-runs shows both outcomes |
| `bt-health-failing` (2 runs) | Failing verdict + root-cause tracing | **Failing** at 0% success; Root Cause surfaces it for the downstream table |
| `bt-health-slow` (240s idle) | Per-run cost spike flag, real per-run cost | The long run is flagged as a cost/duration outlier against its siblings |
| Comments on every fixture | Business view descriptions, AI "Explain this lineage", AI overview | Business view shows the plain-English description per dataset instead of a generated fallback; the AI explanation has real semantics to work with |

## Not covered, and why

| Capability | Status |
|---|---|
| **Delta Sharing as a lineage target** | Not buildable here — it needs a *provider* share from another account. The product already detects this (`table_type` FOREIGN → `external_source` skip) rather than showing an empty graph. The reverse direction (reading a shared table into a local one) is testable if `fevm_shared_catalog` ever gets populated tables |
| **Lakehouse Federation foreign tables** | Needs a UC connection to an external engine (Postgres/MySQL/Snowflake). No connection exists in this workspace; add one and a foreign catalog appears with no further fixture work |
| **Federated Sync (peer trust handshake)** | Needs a second BrickTrace deployment to peer with |
| **ML lineage / serving endpoints** | Tier 3, off by default: a serving endpoint bills continuously while it exists. Test with an existing endpoint if the workspace has one |
| **Runtime Plan Capture** | Deliberately left to the in-app installer (`POST /api/pipeline/install-capture`) — that admin action is itself the thing worth testing, and it mutates a notebook |
| **Ad-hoc SQL producers (`entity_type = NULL`)** | Already covered by the 20 `stress_test` tables, and already known to be unbuildable by the app SP (query history is identity-scoped) |

## Traps this test bed hit while being built

Both were found only by executing the SQL — neither is visible by reading it.

- **`''` is not a quote escape in Databricks.** `'a ''b'' c'` parses as *adjacent*
  string literals and concatenates them, **silently dropping the quotes**: the
  `INSERT` succeeds and the stored value is corrupt. It surfaced much later, as
  `UNRESOLVED_COLUMN \`yyyy\`` when the config expression was evaluated. Use
  backslash escaping (`\'`) — verified: `length('fmt(x, \'y\')')` = 17 vs
  `length('fmt(x, ''y'')')` = 15.
- **Exotic column names and `DROP COLUMN` both need Delta column mapping.**
  `` `Order Date` `` fails with `DELTA_INVALID_CHARACTERS_IN_COLUMN_NAMES` and
  `DROP COLUMN` with `DELTA_UNSUPPORTED_DROP_COLUMN` unless the table sets
  `delta.columnMapping.mode = 'name'` (and type widening needs
  `delta.enableTypeWidening`). Which is also *why* names like `Order Date` are
  rare enough that a validator assuming `snake_case` looks correct for a long time.

## Cost

Tier 1 is six short serverless job runs plus two serverless DLT pipeline updates.
Tier 2 adds six short runs, one of which idles for `--sleep` seconds (default 240
— serverless bills wall-clock, so this is the only fixture with a knob worth
turning down). Everything is small-data: the largest source is
`samples.nyctaxi.trips`, aggregated. The DLT pipelines are created with
`development: true` so their compute tears down promptly.

`--skip-runs` creates every object and triggers nothing, if you want to inspect
the definitions before spending anything.

## Files

| Path | What |
|---|---|
| `build_testbed.sh` | The driver: SQL → grants → uploads → jobs/pipelines → runs. Idempotent, `--dry-run`, `--cleanup` |
| `split_sql.py` | Splits the `.sql` fixtures into single statements (the SQL API takes one per call), quote- and comment-aware |
| `sql/10_fixtures.sql` | View, config table, fan-out hub, PII, DQ edge cases, orphan, append-only DLT sources |
| `sql/20_schema_evolution.sql` | The ALTER sequence for the schema-change detector |
| `sql/sqltask_order_agg.sql` | `sql_task` producer source |
| `notebooks/py_file_customer_norm.py` | `spark_python_task` producer source (plain FILE) |
| `notebooks/nb_multi_producer_{a,b}.py` | The divergent-logic pair |
| `notebooks/nb_framework_driver.py` | Config-table-driven ETL |
| `notebooks/nb_xcat_trips.py` | Cross-catalog producer |
| `notebooks/nb_health_{flaky,failing,slow}.py` | Run-health fixtures |
| `dlt/sql_dlt_channel.sql` | SQL-defined DLT (streaming table + MV) |
| `dlt/dlt_cdc_customers.py` | `apply_changes` SCD2 pipeline |
