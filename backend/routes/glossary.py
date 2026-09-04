"""Routes for Business Lineage / Glossary — capability 10.

Provides a governed business glossary: KPI definitions, data products,
domain ownership, and term→table/column mappings.

Endpoints:
  GET    /api/glossary/terms          — list all business terms (with optional search)
  GET    /api/glossary/terms/{id}     — get a single term with linked assets
  POST   /api/glossary/terms          — create or update a business term
  DELETE /api/glossary/terms/{id}     — remove a business term (ADMIN ONLY)
  GET    /api/glossary/domains        — list all data domains
  POST   /api/glossary/domains        — create or update a data domain
  GET    /api/glossary/kpis           — list KPI definitions
  POST   /api/glossary/kpis           — create or update a KPI definition
  GET    /api/glossary/for-table      — terms linked to a specific table
  POST   /api/glossary/link           — link a term to a table/column
  GET    /api/glossary/propagate-suggestions — suggest term propagation downstream
  GET    /api/glossary/lineage-overlay — term overlay for lineage graph nodes

Persisted in app-owned Delta tables:
  - glossary_terms (term_id, name, definition, domain, owner, ...)
  - glossary_domains (domain_id, name, description, owner, ...)
  - glossary_kpis (kpi_id, name, formula_sql, source_tables, ...)
  - glossary_term_links (term_id, asset_type, asset_fqn, column_name)

Authorization: the reads and the upserts are open to every app user — this is a
collaborative catalog and the UI wires the term form up for everyone — so each
written row records the real caller in created_by/owner instead of a generic
'app'. The destructive DELETE is admin-gated via require_admin().
"""
from __future__ import annotations

import os
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.validators import _validate, require_admin, sql_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/glossary", tags=["glossary"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
TERMS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.glossary_terms"
DOMAINS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.glossary_domains"
KPIS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.glossary_kpis"
LINKS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.glossary_term_links"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

# Lifecycle values a term filter may ask for. The UI's status dropdown offers
# exactly these three (GlossaryPanel.tsx), so anything else is a hand-crafted
# request — allow-list it instead of escaping it into the WHERE clause.
TERM_STATUSES = ("draft", "approved", "deprecated")


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
    )
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def _ensure_tables() -> None:
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {TERMS_TABLE} (
            term_id STRING, name STRING, definition STRING, domain STRING,
            owner STRING, status STRING, synonyms STRING, created_by STRING,
            created_at TIMESTAMP, updated_at TIMESTAMP
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {DOMAINS_TABLE} (
            domain_id STRING, name STRING, description STRING, owner STRING,
            color STRING, created_at TIMESTAMP, updated_at TIMESTAMP
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {KPIS_TABLE} (
            kpi_id STRING, name STRING, definition STRING, formula_sql STRING,
            source_tables STRING, owner STRING, domain STRING, granularity STRING,
            created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {LINKS_TABLE} (
            link_id STRING, term_id STRING, asset_type STRING, asset_fqn STRING,
            column_name STRING, created_by STRING, created_at TIMESTAMP
        ) USING DELTA""")
    except Exception as e:
        logger.warning(f"glossary: could not ensure tables: {e}")


_tables_ensured = False


def _lazy_ensure():
    global _tables_ensured
    if not _tables_ensured:
        _ensure_tables()
        _tables_ensured = True


def _uuid_or_new(value: Optional[str], name: str) -> str:
    """Return a canonical UUID: the caller's value if it is one, else a fresh one.

    The three upsert endpoints interpolate this id into BOTH the MERGE source
    (`USING (SELECT '<id>')`) and the `INSERT ... VALUES` clause, which made it
    the one field an attacker could steer into two SQL positions at once. Ids are
    server-generated in the normal flow — the UI never sends one — so allow-listing
    the shape closes the hole outright and sql_str() at each interpolation site is
    then only defence in depth.
    """
    if not value:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: must be a UUID")


def _like_escape(s: str) -> str:
    """Escape LIKE pattern metacharacters, for values used inside a LIKE pattern.

    A LIKE pattern is a SECOND escape layer on top of the SQL literal, and
    sql_str only handles the literal. Feeding its output straight into
    `LIKE '%…%'` was therefore wrong in both directions:

      * `C:\\data` became the literal `%c:\\data%`, whose `\\d` is an escape
        character in the middle of a pattern — Spark raises
        INVALID_FORMAT.ESC_IN_THE_MIDDLE, which the handler's generic except
        turned into an undiagnosable 500. Before the escaping change, plain
        quote-doubling left the backslash to be swallowed and search worked.
      * `%` and `_` were left unescaped, so they silently acted as wildcards.

    Escape for the pattern layer FIRST; sql_str then escapes for the literal
    layer, and the two unwind in the right order. discovery.py:62 already does
    this for `%` — same reason.
    """
    return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_admin(request: Request) -> bool:
    """Non-raising admin probe, for deciding whether to return attribution
    columns. Use require_admin() when the whole endpoint should be gated."""
    from backend.main import _get_user_info
    try:
        return bool(_get_user_info(request)[1])
    except Exception:
        return False


# Columns that hold a real workspace email purely for audit purposes. They are
# not product data — nothing in the UI renders them — so they are withheld from
# non-admins. Without this, capture recording the true caller (right) turned four
# ungated SELECT * reads into a directory of every editor's email (wrong).
# NOTE: `owner` is deliberately NOT here. It is a user-typed business field shown
# in GlossaryPanel.tsx; the leak via `owner` is fixed at the write end instead, by
# no longer defaulting it to the caller's address.
_ATTRIBUTION_COLS = ("created_by", "updated_by")


def _strip_attribution(rows: list[dict], request: Request) -> list[dict]:
    """Drop audit-only attribution columns unless the caller is an admin."""
    if _is_admin(request):
        return rows
    return [
        {k: v for k, v in row.items() if k not in _ATTRIBUTION_COLS}
        for row in rows
    ]


def _caller(request: Request) -> str:
    """Return the requesting user's email for the row's attribution columns.

    The upserts here are open to every app user (see upsert_term), so the row must
    record who actually made the change rather than a hardcoded 'app' — the SQL
    itself runs as the app service principal and carries no identity.
    """
    from backend.main import _get_user_info
    email, _ = _get_user_info(request)
    return email or "unknown"


# --- Models ---
class TermIn(BaseModel):
    term_id: Optional[str] = None
    name: str
    definition: str
    domain: Optional[str] = ""
    owner: Optional[str] = ""
    status: Optional[str] = "draft"  # draft | approved | deprecated
    synonyms: Optional[str] = ""


class DomainIn(BaseModel):
    domain_id: Optional[str] = None
    name: str
    description: Optional[str] = ""
    owner: Optional[str] = ""
    color: Optional[str] = "#6366f1"


class KpiIn(BaseModel):
    kpi_id: Optional[str] = None
    name: str
    definition: str
    formula_sql: Optional[str] = ""
    source_tables: Optional[str] = ""  # comma-separated FQNs
    owner: Optional[str] = ""
    domain: Optional[str] = ""
    granularity: Optional[str] = ""  # daily | weekly | monthly


class TermLinkIn(BaseModel):
    term_id: str
    asset_type: str  # table | column | dashboard | job
    asset_fqn: str
    column_name: Optional[str] = ""


# --- Term endpoints ---
@router.get("/terms")
async def list_terms(
    request: Request,
    q: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """List all business terms with optional search/filter."""
    _lazy_ensure()
    # sql_str, not quote-doubling: these filters land in a `SELECT *` whose rows go
    # straight back to the caller, so a value starting `\'` would otherwise close
    # the literal and let the rest of it execute as SQL.
    conditions = ["1=1"]
    if q:
        # Pattern layer first (see _like_escape), then the literal layer.
        safe_q = sql_str(_like_escape(q[:100]).lower())
        conditions.append(f"(lower(name) LIKE '%{safe_q}%' OR lower(definition) LIKE '%{safe_q}%')")
    if domain:
        conditions.append(f"domain = '{sql_str(domain, limit=100)}'")
    if status:
        if status not in TERM_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: expected one of {', '.join(TERM_STATUSES)}",
            )
        conditions.append(f"status = '{sql_str(status, limit=50)}'")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {TERMS_TABLE} WHERE {where} ORDER BY name LIMIT {limit}"
        )
        rows = _strip_attribution(rows, request)
        return {"terms": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"glossary: list_terms failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list glossary terms")


@router.get("/terms/{term_id}")
async def get_term(request: Request, term_id: str):
    """Get a single term with its linked assets."""
    _lazy_ensure()
    safe_id = sql_str(term_id, limit=100)
    try:
        terms = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {TERMS_TABLE} WHERE term_id = '{safe_id}'"
        )
        if not terms:
            raise HTTPException(status_code=404, detail="Term not found")
        links = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {LINKS_TABLE} WHERE term_id = '{safe_id}'"
        )
        return {
            "term": _strip_attribution(terms, request)[0],
            "links": _strip_attribution(links, request),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"glossary: get_term failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load glossary term")


@router.post("/terms")
async def upsert_term(request: Request, body: TermIn):
    """Create or update a business term.

    Deliberately NOT admin-gated: the glossary is a collaborative catalog and the
    UI exposes this to every app user (GlossaryPanel.tsx). `created_by` therefore
    records the real caller so each row stays attributable. Only the destructive
    DELETE below is admin-gated.
    """
    _lazy_ensure()
    tid = _uuid_or_new(body.term_id, "term_id")
    actor = _caller(request)
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {TERMS_TABLE} t USING (SELECT '{sql_str(tid)}' AS term_id) s ON t.term_id = s.term_id
            WHEN MATCHED THEN UPDATE SET
                name = '{sql_str(body.name)}',
                definition = '{sql_str(body.definition, limit=2000)}',
                domain = '{sql_str(body.domain)}',
                owner = '{sql_str(body.owner)}',
                status = '{sql_str(body.status or "draft")}',
                synonyms = '{sql_str(body.synonyms)}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (term_id, name, definition, domain, owner, status, synonyms, created_by, created_at, updated_at)
            VALUES ('{sql_str(tid)}', '{sql_str(body.name)}', '{sql_str(body.definition, limit=2000)}',
                    '{sql_str(body.domain)}', '{sql_str(body.owner)}',
                    '{sql_str(body.status or "draft")}', '{sql_str(body.synonyms)}',
                    '{sql_str(actor)}', TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "term_id": tid}
    except Exception as e:
        logger.error(f"glossary: upsert_term failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save glossary term")


@router.delete("/terms/{term_id}")
async def delete_term(request: Request, term_id: str):
    """Delete a term and every asset link pointing at it. Admin-gated.

    Unlike the upserts this is destructive and unrecoverable — the Delta rows are
    removed, not soft-deleted — so it requires an app admin. UI consequence:
    GlossaryPanel.tsx renders the delete control for every user, so a non-admin
    clicking it now gets a 403; the control should be hidden for non-admins.
    """
    require_admin(request)
    _lazy_ensure()
    safe_id = sql_str(term_id, limit=100)
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {TERMS_TABLE} WHERE term_id = '{safe_id}'")
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {LINKS_TABLE} WHERE term_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"glossary: delete_term failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete glossary term")


# --- Domain endpoints ---
@router.get("/domains")
async def list_domains(request: Request):
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(_execute_sql, f"SELECT * FROM {DOMAINS_TABLE} ORDER BY name")
        return {"domains": rows}
    except Exception as e:
        logger.error(f"glossary: list_domains failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list data domains")


@router.post("/domains")
async def upsert_domain(request: Request, body: DomainIn):
    """Create or update a data domain. Open to all app users, like upsert_term."""
    _lazy_ensure()
    did = _uuid_or_new(body.domain_id, "domain_id")
    # `owner` is a user-typed business field (GlossaryPanel.tsx renders it), NOT an
    # attribution column, and this endpoint is open to every app user. Defaulting
    # it to _caller(request) therefore did two unwanted things: it wrote a real
    # workspace email into a field the ungated GET /domains hands to everyone, and
    # it made a blank owner read as a positive claim of ownership rather than as
    # "unknown". glossary_domains has no created_by column to record the editor in,
    # so the honest value when the body omits one is empty.
    owner = body.owner or ""
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {DOMAINS_TABLE} t USING (SELECT '{sql_str(did)}' AS domain_id) s ON t.domain_id = s.domain_id
            WHEN MATCHED THEN UPDATE SET
                name = '{sql_str(body.name)}',
                description = '{sql_str(body.description, limit=1000)}',
                -- Never overwrite an existing owner. The MERGE key is a
                -- caller-supplied UUID and GET /domains hands out every
                -- domain_id, so an unconditional assignment let any user re-POST
                -- someone else's domain and take it over — a Delta overwrite that
                -- leaves no trace of the previous owner in the row. Filling in a
                -- blank owner is still allowed; changing a set one is not.
                owner = CASE
                    WHEN t.owner IS NULL OR t.owner = '' THEN '{sql_str(owner)}'
                    ELSE t.owner
                END,
                color = '{sql_str(body.color or "#6366f1")}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (domain_id, name, description, owner, color, created_at, updated_at)
            VALUES ('{sql_str(did)}', '{sql_str(body.name)}', '{sql_str(body.description, limit=1000)}',
                    '{sql_str(owner)}', '{sql_str(body.color or "#6366f1")}',
                    TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "domain_id": did}
    except Exception as e:
        logger.error(f"glossary: upsert_domain failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save data domain")


# --- KPI endpoints ---
@router.get("/kpis")
async def list_kpis(request: Request, domain: Optional[str] = Query(None)):
    _lazy_ensure()
    where = f"WHERE domain = '{sql_str(domain, limit=100)}'" if domain else ""
    try:
        rows = await asyncio.to_thread(_execute_sql, f"SELECT * FROM {KPIS_TABLE} {where} ORDER BY name")
        return {"kpis": rows}
    except Exception as e:
        logger.error(f"glossary: list_kpis failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list KPI definitions")


@router.post("/kpis")
async def upsert_kpi(request: Request, body: KpiIn):
    """Create or update a KPI definition. Open to all app users, like upsert_term."""
    _lazy_ensure()
    kid = _uuid_or_new(body.kpi_id, "kpi_id")
    actor = _caller(request)
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {KPIS_TABLE} t USING (SELECT '{sql_str(kid)}' AS kpi_id) s ON t.kpi_id = s.kpi_id
            WHEN MATCHED THEN UPDATE SET
                name = '{sql_str(body.name)}',
                definition = '{sql_str(body.definition, limit=2000)}',
                formula_sql = '{sql_str(body.formula_sql, limit=4000)}',
                source_tables = '{sql_str(body.source_tables)}',
                owner = '{sql_str(body.owner)}',
                domain = '{sql_str(body.domain)}',
                granularity = '{sql_str(body.granularity)}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (kpi_id, name, definition, formula_sql, source_tables, owner, domain, granularity, created_by, created_at, updated_at)
            VALUES ('{sql_str(kid)}', '{sql_str(body.name)}', '{sql_str(body.definition, limit=2000)}',
                    '{sql_str(body.formula_sql, limit=4000)}', '{sql_str(body.source_tables)}',
                    '{sql_str(body.owner)}', '{sql_str(body.domain)}',
                    '{sql_str(body.granularity)}', '{sql_str(actor)}', TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "kpi_id": kid}
    except Exception as e:
        logger.error(f"glossary: upsert_kpi failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save KPI definition")


# --- Term-to-asset linking ---
@router.get("/for-table")
async def terms_for_table(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return all glossary terms linked to a specific table."""
    _lazy_ensure()
    # Identifiers are allow-listed (_validate); the assembled FQN still goes through
    # sql_str so the literal can't be broken out of even if the regex ever loosens.
    fqn = f"{_validate(catalog, 'catalog')}.{_validate(schema, 'schema')}.{_validate(table, 'table')}"
    safe_fqn = sql_str(fqn, limit=300)
    try:
        links = await asyncio.to_thread(_execute_sql, f"""
            SELECT l.*, t.name AS term_name, t.definition AS term_definition, t.domain
            FROM {LINKS_TABLE} l JOIN {TERMS_TABLE} t ON l.term_id = t.term_id
            WHERE l.asset_fqn = '{safe_fqn}'
            ORDER BY t.name
        """)
        return {"links": links}
    except Exception as e:
        logger.error(f"glossary: terms_for_table failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load terms for table")


@router.post("/link")
async def link_term(request: Request, body: TermLinkIn):
    """Link a business term to a table or column. Open to all app users."""
    _lazy_ensure()
    lid = str(uuid.uuid4())
    actor = _caller(request)
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            INSERT INTO {LINKS_TABLE} (link_id, term_id, asset_type, asset_fqn, column_name, created_by, created_at)
            VALUES ('{sql_str(lid)}', '{sql_str(body.term_id, limit=100)}', '{sql_str(body.asset_type, limit=50)}',
                    '{sql_str(body.asset_fqn, limit=300)}', '{sql_str(body.column_name, limit=255)}',
                    '{sql_str(actor)}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "link_id": lid}
    except Exception as e:
        logger.error(f"glossary: link_term failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to link term to asset")


# ===========================================================================
# Business Lineage closure: Term Propagation + Graph Overlay (v2.5.2)
# ===========================================================================

@router.get("/propagate-suggestions")
async def propagate_suggestions(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Suggest glossary term propagation downstream via table lineage.

    Given a source table with linked terms, walks downstream lineage and
    identifies tables that don't yet have those terms linked.
    Returns suggestions only — use POST /link to apply each one.

    Bridges business context to technical lineage: a "Revenue" term tagged
    on a source table can propagate to every downstream gold table.
    """
    _lazy_ensure()
    fqn = f"{_validate(catalog, 'catalog')}.{_validate(schema, 'schema')}.{_validate(table, 'table')}"
    safe_fqn = sql_str(fqn)

    try:
        # 1. Get terms linked to the source table
        source_terms = await asyncio.to_thread(_execute_sql, f"""
            SELECT DISTINCT l.term_id, t.name AS term_name, t.domain
            FROM {LINKS_TABLE} l JOIN {TERMS_TABLE} t ON l.term_id = t.term_id
            WHERE l.asset_fqn = '{safe_fqn}' AND l.asset_type = 'table'
        """)
        if not source_terms:
            return {"status": "ok", "suggestions": [], "note": "No terms linked to source table"}

        term_ids = [r["term_id"] for r in source_terms]

        # 2. Walk downstream tables (BFS, 3 hops max)
        downstream_tables: set = set()
        frontier = [fqn]
        visited = {fqn}
        for _ in range(3):
            if not frontier:
                break
            next_frontier = []
            for src in frontier[:20]:
                try:
                    rows = _execute_sql(f"""
                        SELECT DISTINCT target_table_full_name
                        FROM system.access.table_lineage
                        WHERE source_table_full_name = '{sql_str(src)}'
                          AND event_time > current_timestamp() - INTERVAL 90 DAYS
                        LIMIT 30
                    """)
                    for r in rows:
                        tgt = r.get("target_table_full_name", "")
                        if tgt and tgt not in visited:
                            visited.add(tgt)
                            downstream_tables.add(tgt)
                            next_frontier.append(tgt)
                except Exception:
                    pass
            frontier = next_frontier

        if not downstream_tables:
            return {"status": "ok", "suggestions": [], "note": "No downstream tables found"}

        # 3. Find which downstream tables are missing these terms
        suggestions = []
        for tgt_table in sorted(downstream_tables)[:50]:
            safe_tgt = sql_str(tgt_table)
            existing = _execute_sql(f"""
                SELECT term_id FROM {LINKS_TABLE}
                WHERE asset_fqn = '{safe_tgt}' AND asset_type = 'table'
            """)
            existing_ids = {r["term_id"] for r in existing}
            missing = [t for t in source_terms if t["term_id"] not in existing_ids]
            if missing:
                suggestions.append({
                    "target_table": tgt_table,
                    "missing_terms": [{"term_id": t["term_id"], "name": t["term_name"], "domain": t.get("domain", "")} for t in missing],
                })

        return {
            "source_table": fqn,
            "source_terms": source_terms,
            "downstream_count": len(downstream_tables),
            "suggestions": suggestions,
            "suggestion_count": len(suggestions),
        }
    except Exception as e:
        logger.error(f"glossary: propagate_suggestions failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute propagation suggestions")


@router.get("/lineage-overlay")
async def lineage_overlay(
    request: Request,
    catalog: str = Query(...),
    schema: Optional[str] = Query(None),
):
    """Return business glossary overlay for a lineage graph.

    Given a catalog (optionally schema), returns all term links within scope,
    grouped by table — enabling the frontend to render term badges, domain
    colors, and KPI indicators on lineage graph nodes.
    """
    _lazy_ensure()
    # These two were interpolated raw — no escaping at all — straight into a LIKE
    # pattern and, below, into the KPI query. Allow-list them as identifiers and
    # escape on the way in.
    cat = sql_str(_validate(catalog, "catalog"))
    sch = sql_str(_validate(schema, "schema")) if schema else None
    scope_filter = f"l.asset_fqn LIKE '{cat}.{sch}.%'" if sch else f"l.asset_fqn LIKE '{cat}.%'"
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT l.asset_fqn, l.column_name, l.asset_type,
                   t.term_id, t.name AS term_name, t.domain, t.status,
                   d.color AS domain_color
            FROM {LINKS_TABLE} l
            JOIN {TERMS_TABLE} t ON l.term_id = t.term_id
            LEFT JOIN {DOMAINS_TABLE} d ON t.domain = d.name
            WHERE {scope_filter}
            ORDER BY l.asset_fqn, t.name
            LIMIT 500
        """)

        # Group by table for graph overlay rendering
        by_table: dict = {}
        for r in rows:
            fqn = r.get("asset_fqn", "")
            if fqn not in by_table:
                by_table[fqn] = {"table": fqn, "terms": [], "domains": []}
            by_table[fqn]["terms"].append({
                "term_id": r.get("term_id"),
                "name": r.get("term_name"),
                "domain": r.get("domain"),
                "domain_color": r.get("domain_color"),
                "column": r.get("column_name") or None,
                "status": r.get("status"),
            })
            domain = r.get("domain")
            if domain and domain not in by_table[fqn]["domains"]:
                by_table[fqn]["domains"].append(domain)

        overlay = list(by_table.values())

        # Also fetch KPIs in scope
        kpis = await asyncio.to_thread(_execute_sql, f"""
            SELECT kpi_id, name, formula_sql, source_tables, domain, granularity
            FROM {KPIS_TABLE}
            WHERE source_tables LIKE '%{cat}%'
            LIMIT 100
        """)

        return {
            "catalog": catalog,
            "schema": schema,
            "overlay": overlay,
            "table_count": len(overlay),
            "kpis": kpis,
        }
    except Exception as e:
        logger.error(f"glossary: lineage_overlay failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to build glossary overlay")
