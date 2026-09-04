-- =============================================================================
-- Lineage Explorer — one-time grants for the app's service principal.
--
-- The app runs as its own service principal (SP) and reads ONLY metadata +
-- system tables — never your row data. After `databricks bundle deploy`, a
-- METASTORE ADMIN runs this once so the deployed app can actually see lineage,
-- cost, and sharing.
--
-- Replace the two placeholders below, then run in a SQL editor / DBSQL:
--   :APP_SP   the app's service principal Application ID
--             (Apps UI → your app → "App resources"/OAuth, or
--              `databricks apps get <app-name>` → service_principal_client_id)
--   :CATALOG  a catalog you want explorable — repeat the CATALOG block per catalog
--
-- Least privilege: BROWSE exposes names/metadata for lineage, NOT table data.
-- Grant SELECT on a catalog only if you also want the in-app data preview to read rows.
-- =============================================================================

-- 1) System tables — lineage, cost, and sharing metadata (read-only) ----------
GRANT USE CATALOG ON CATALOG system                       TO `:APP_SP`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access          TO `:APP_SP`;  -- table_lineage, column_lineage, audit
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing         TO `:APP_SP`;  -- usage, list_prices (serverless cost)
GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO `:APP_SP`;  -- shares / recipients / providers / *_share_usage

-- 2) Per-catalog metadata browse (repeat for each explorable catalog) ---------
GRANT USE CATALOG ON CATALOG `:CATALOG`                    TO `:APP_SP`;
GRANT BROWSE      ON CATALOG `:CATALOG`                    TO `:APP_SP`;
-- Optional — only if you want the app to read sample rows / the data preview:
-- GRANT SELECT    ON CATALOG `:CATALOG`                    TO `:APP_SP`;

-- 3) (Optional) Live mode — members of this group can bypass cache.
--    Set ADMIN_GROUP_NAME in databricks.yml to match your admin group.

-- Notes
-- * `system.access` and `system.billing` must be ENABLED by an account admin
--   first (Account console → Settings → System tables). Without them, lineage
--   and cost are empty regardless of these grants.
-- * Sharing views (system.information_schema.shares, table_share_usage, ...)
--   only show shares/recipients the SP is privileged on. For a full sharing
--   inventory the SP needs to be a metastore admin or be granted the relevant
--   share/recipient objects.

-- =============================================================================
-- 4) App-owned schema for governance, glossary, notifications, snapshots, etc.
--    Replace :LINEAGE_CATALOG with your configured LINEAGE_CATALOG (default: lattice_lineage)
-- =============================================================================
CREATE CATALOG IF NOT EXISTS `:LINEAGE_CATALOG`;
GRANT USE CATALOG ON CATALOG `:LINEAGE_CATALOG`          TO `:APP_SP`;
GRANT CREATE SCHEMA ON CATALOG `:LINEAGE_CATALOG`        TO `:APP_SP`;

CREATE SCHEMA IF NOT EXISTS `:LINEAGE_CATALOG`.lineage;
GRANT ALL PRIVILEGES ON SCHEMA `:LINEAGE_CATALOG`.lineage TO `:APP_SP`;

-- App-managed tables (auto-created by the app on first use, DDL here for reference)
-- Governance
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.governance_rules (
    rule_id STRING, catalog STRING, schema_name STRING, table_pattern STRING,
    column_pattern STRING, tag_name STRING, classification STRING,
    created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP
) USING DELTA;

-- Data Quality
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.dq_rules (
    rule_id STRING, table_fqn STRING, column_name STRING, rule_type STRING,
    expression STRING, severity STRING, created_by STRING,
    created_at TIMESTAMP, updated_at TIMESTAMP, notes STRING
) USING DELTA;

-- Business Glossary
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.glossary_terms (
    term_id STRING, name STRING, definition STRING, domain STRING,
    owner STRING, status STRING, synonyms STRING, created_by STRING,
    created_at TIMESTAMP, updated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.glossary_domains (
    domain_id STRING, name STRING, description STRING, owner STRING,
    color STRING, created_at TIMESTAMP, updated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.glossary_kpis (
    kpi_id STRING, name STRING, definition STRING, formula_sql STRING,
    source_tables STRING, owner STRING, domain STRING, granularity STRING,
    created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.glossary_term_links (
    link_id STRING, term_id STRING, asset_type STRING, asset_fqn STRING,
    column_name STRING, created_by STRING, created_at TIMESTAMP
) USING DELTA;

-- Notifications & Alerts
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.notifications (
    notif_id STRING, notif_type STRING, severity STRING, title STRING,
    detail STRING, table_fqn STRING, column_name STRING, detected_at TIMESTAMP,
    is_read BOOLEAN, read_by STRING, read_at TIMESTAMP, metadata STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.notification_rules (
    rule_id STRING, rule_type STRING, target_pattern STRING,
    threshold DOUBLE, severity STRING, enabled BOOLEAN,
    created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP, notes STRING
) USING DELTA;

-- Graph Snapshots (versioned lineage)
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.graph_snapshots (
    snapshot_id STRING, scope STRING, label STRING, captured_at TIMESTAMP,
    captured_by STRING, node_count INT, edge_count INT, graph_json STRING,
    metadata STRING
) USING DELTA;

-- External Sources (multi-platform lineage)
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.external_sources (
    source_id STRING, platform STRING, name STRING, description STRING,
    connection_info STRING, metadata STRING,
    created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.external_lineage_edges (
    edge_id STRING, source_platform STRING, source_asset STRING,
    source_asset_type STRING, target_asset STRING, target_asset_type STRING,
    relationship STRING, transformation STRING, confidence STRING,
    created_at TIMESTAMP, metadata STRING
) USING DELTA;

-- OpenLineage import events
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.external_lineage_events (
    event_id STRING, event_type STRING, event_time TIMESTAMP,
    job_namespace STRING, job_name STRING, run_id STRING,
    input_datasets STRING, output_datasets STRING,
    raw_event STRING, imported_at TIMESTAMP
) USING DELTA;

-- Model lineage (AI/ML)
CREATE TABLE IF NOT EXISTS `:LINEAGE_CATALOG`.lineage.model_lineage (
    record_id STRING, model_name STRING, model_version STRING,
    training_table STRING, job_id STRING, run_id STRING,
    notebook_path STRING, actor STRING, notes STRING,
    created_at TIMESTAMP
) USING DELTA;
