"""Routes for Business Lineage / Glossary — capability 10.

Provides a governed business glossary: KPI definitions, data products,
domain ownership, and term→table/column mappings.

Endpoints:
  GET    /api/glossary/terms          — list all business terms (with optional search)
  GET    /api/glossary/terms/{id}     — get a single term with linked assets
  POST   /api/glossary/terms          — create or update a business term
  DELETE /api/glossary/terms/{id}     — remove a business term
  GET    /api/glossary/domains        — list all data domains
  POST   /api/glossary/domains        — create or update a data domain
  GET    /api/glossary/kpis           — list KPI definitions
  POST   /api/glossary/kpis           — create or update a KPI definition
  GET    /api/glossary/for-table      — terms linked to a specific table

Persisted in app-owned Delta tables:
  - glossary_terms (term_id, name, definition, domain, owner, ...)
  - glossary_domains (domain_id, name, description, owner, ...)
  - glossary_kpis (kpi_id, name, formula_sql, source_tables, ...)
  - glossary_term_links (term_id, asset_type, asset_fqn, column_name)
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
    conditions = ["1=1"]
    if q:
        safe_q = q.replace("'", "''")[:100]
        conditions.append(f"(lower(name) LIKE '%{safe_q.lower()}%' OR lower(definition) LIKE '%{safe_q.lower()}%')")
    if domain:
        safe_d = domain.replace("'", "''")[:100]
        conditions.append(f"domain = '{safe_d}'")
    if status:
        safe_s = status.replace("'", "''")[:50]
        conditions.append(f"status = '{safe_s}'")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {TERMS_TABLE} WHERE {where} ORDER BY name LIMIT {limit}"
        )
        return {"terms": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/terms/{term_id}")
async def get_term(request: Request, term_id: str):
    """Get a single term with its linked assets."""
    _lazy_ensure()
    safe_id = term_id.replace("'", "''")[:100]
    try:
        terms = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {TERMS_TABLE} WHERE term_id = '{safe_id}'"
        )
        if not terms:
            raise HTTPException(status_code=404, detail="Term not found")
        links = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {LINKS_TABLE} WHERE term_id = '{safe_id}'"
        )
        return {"term": terms[0], "links": links}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/terms")
async def upsert_term(request: Request, body: TermIn):
    """Create or update a business term."""
    _lazy_ensure()
    tid = body.term_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {TERMS_TABLE} t USING (SELECT '{tid}' AS term_id) s ON t.term_id = s.term_id
            WHEN MATCHED THEN UPDATE SET
                name = '{body.name.replace(chr(39), chr(39)*2)}',
                definition = '{body.definition.replace(chr(39), chr(39)*2)[:2000]}',
                domain = '{(body.domain or "").replace(chr(39), chr(39)*2)}',
                owner = '{(body.owner or "").replace(chr(39), chr(39)*2)}',
                status = '{(body.status or "draft").replace(chr(39), chr(39)*2)}',
                synonyms = '{(body.synonyms or "").replace(chr(39), chr(39)*2)}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (term_id, name, definition, domain, owner, status, synonyms, created_by, created_at, updated_at)
            VALUES ('{tid}', '{body.name.replace(chr(39), chr(39)*2)}', '{body.definition.replace(chr(39), chr(39)*2)[:2000]}',
                    '{(body.domain or "").replace(chr(39), chr(39)*2)}', '{(body.owner or "").replace(chr(39), chr(39)*2)}',
                    '{(body.status or "draft").replace(chr(39), chr(39)*2)}', '{(body.synonyms or "").replace(chr(39), chr(39)*2)}',
                    'app', TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "term_id": tid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/terms/{term_id}")
async def delete_term(request: Request, term_id: str):
    _lazy_ensure()
    safe_id = term_id.replace("'", "''")[:100]
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {TERMS_TABLE} WHERE term_id = '{safe_id}'")
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {LINKS_TABLE} WHERE term_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Domain endpoints ---
@router.get("/domains")
async def list_domains(request: Request):
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(_execute_sql, f"SELECT * FROM {DOMAINS_TABLE} ORDER BY name")
        return {"domains": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/domains")
async def upsert_domain(request: Request, body: DomainIn):
    _lazy_ensure()
    did = body.domain_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {DOMAINS_TABLE} t USING (SELECT '{did}' AS domain_id) s ON t.domain_id = s.domain_id
            WHEN MATCHED THEN UPDATE SET
                name = '{body.name.replace(chr(39), chr(39)*2)}',
                description = '{(body.description or "").replace(chr(39), chr(39)*2)[:1000]}',
                owner = '{(body.owner or "").replace(chr(39), chr(39)*2)}',
                color = '{(body.color or "#6366f1").replace(chr(39), chr(39)*2)}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (domain_id, name, description, owner, color, created_at, updated_at)
            VALUES ('{did}', '{body.name.replace(chr(39), chr(39)*2)}', '{(body.description or "").replace(chr(39), chr(39)*2)[:1000]}',
                    '{(body.owner or "").replace(chr(39), chr(39)*2)}', '{(body.color or "#6366f1").replace(chr(39), chr(39)*2)}',
                    TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "domain_id": did}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- KPI endpoints ---
@router.get("/kpis")
async def list_kpis(request: Request, domain: Optional[str] = Query(None)):
    _lazy_ensure()
    where = f"WHERE domain = '{domain.replace(chr(39), chr(39)*2)}'" if domain else ""
    try:
        rows = await asyncio.to_thread(_execute_sql, f"SELECT * FROM {KPIS_TABLE} {where} ORDER BY name")
        return {"kpis": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/kpis")
async def upsert_kpi(request: Request, body: KpiIn):
    _lazy_ensure()
    kid = body.kpi_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {KPIS_TABLE} t USING (SELECT '{kid}' AS kpi_id) s ON t.kpi_id = s.kpi_id
            WHEN MATCHED THEN UPDATE SET
                name = '{body.name.replace(chr(39), chr(39)*2)}',
                definition = '{body.definition.replace(chr(39), chr(39)*2)[:2000]}',
                formula_sql = '{(body.formula_sql or "").replace(chr(39), chr(39)*2)[:4000]}',
                source_tables = '{(body.source_tables or "").replace(chr(39), chr(39)*2)}',
                owner = '{(body.owner or "").replace(chr(39), chr(39)*2)}',
                domain = '{(body.domain or "").replace(chr(39), chr(39)*2)}',
                granularity = '{(body.granularity or "").replace(chr(39), chr(39)*2)}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (kpi_id, name, definition, formula_sql, source_tables, owner, domain, granularity, created_by, created_at, updated_at)
            VALUES ('{kid}', '{body.name.replace(chr(39), chr(39)*2)}', '{body.definition.replace(chr(39), chr(39)*2)[:2000]}',
                    '{(body.formula_sql or "").replace(chr(39), chr(39)*2)[:4000]}', '{(body.source_tables or "").replace(chr(39), chr(39)*2)}',
                    '{(body.owner or "").replace(chr(39), chr(39)*2)}', '{(body.domain or "").replace(chr(39), chr(39)*2)}',
                    '{(body.granularity or "").replace(chr(39), chr(39)*2)}', 'app', TIMESTAMP '{now}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "kpi_id": kid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    fqn = f"{catalog}.{schema}.{table}"
    safe_fqn = fqn.replace("'", "''")[:300]
    try:
        links = await asyncio.to_thread(_execute_sql, f"""
            SELECT l.*, t.name AS term_name, t.definition AS term_definition, t.domain
            FROM {LINKS_TABLE} l JOIN {TERMS_TABLE} t ON l.term_id = t.term_id
            WHERE l.asset_fqn = '{safe_fqn}'
            ORDER BY t.name
        """)
        return {"links": links}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/link")
async def link_term(request: Request, body: TermLinkIn):
    """Link a business term to a table or column."""
    _lazy_ensure()
    lid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            INSERT INTO {LINKS_TABLE} (link_id, term_id, asset_type, asset_fqn, column_name, created_by, created_at)
            VALUES ('{lid}', '{body.term_id.replace(chr(39), chr(39)*2)}', '{body.asset_type.replace(chr(39), chr(39)*2)}',
                    '{body.asset_fqn.replace(chr(39), chr(39)*2)}', '{(body.column_name or "").replace(chr(39), chr(39)*2)}',
                    'app', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "link_id": lid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
