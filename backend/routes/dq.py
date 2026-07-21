"""Data quality rules — capability 23.

Endpoints:
  GET  /api/dq-rules              — list all DQ rules (optionally filtered by table)
  GET  /api/dq-rules/columns      — list column-level DQ rules for a specific table
  POST /api/dq-rules              — add or update a DQ rule
  DELETE /api/dq-rules/{rule_id}  — remove a rule

DQ rules are persisted in an app-owned Delta table `dq_rules` inside the
lineage schema.  They are conceptually separate from Expectations defined
inside SDP pipelines; this table stores app-managed rules you can annotate
columns with regardless of how the table was produced.

Table DDL (auto-created on first write):
    dq_rules (
        rule_id       STRING,
        table_fqn     STRING,
        column_name   STRING,
        rule_type     STRING,   -- NOT_NULL | UNIQUE | RANGE | REGEX | CUSTOM
        expression    STRING,   -- SQL expression checked against the column
        severity      STRING,   -- ERROR | WARN
        created_by    STRING,
        created_at    TIMESTAMP,
        updated_at    TIMESTAMP,
        notes         STRING
    )
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dq-rules", tags=["dq"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
DQ_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_rules"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_FULL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


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


def _ensure_dq_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {DQ_TABLE} ("
            f"  rule_id STRING, table_fqn STRING, column_name STRING, "
            f"  rule_type STRING, expression STRING, severity STRING, "
            f"  created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP, notes STRING"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"dq: could not ensure {DQ_TABLE}: {e}")


class DQRuleIn(BaseModel):
    rule_id: Optional[str] = None
    table_fqn: str
    column_name: Optional[str] = None
    rule_type: str = "CUSTOM"
    expression: str
    severity: str = "ERROR"
    notes: Optional[str] = ""


@router.get("")
async def list_dq_rules(
    request: Request,
    table_fqn: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """List DQ rules, optionally filtered to a specific table."""
    table_filter = ""
    if table_fqn:
        if not _FULL_NAME_RE.match(table_fqn):
            raise HTTPException(status_code=400, detail="Invalid table_fqn")
        table_filter = f"WHERE table_fqn = '{table_fqn}' "
    try:
        _ensure_dq_table()
        rows = _execute_sql(
            f"SELECT * FROM {DQ_TABLE} {table_filter} "
            f"ORDER BY table_fqn, column_name, rule_type LIMIT {limit}"
        )
        return {"rules": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/columns")
async def list_dq_rules_for_columns(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return per-column DQ expectations for a table."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    full_name = f"{c}.{s}.{t}"
    try:
        _ensure_dq_table()
        rows = _execute_sql(
            f"SELECT column_name, rule_type, expression, severity, notes "
            f"FROM {DQ_TABLE} "
            f"WHERE table_fqn = '{full_name}' "
            f"ORDER BY column_name, rule_type"
        )
        # Group by column
        by_col: dict[str, list] = {}
        for r in rows:
            col = r.get("column_name") or "__table__"
            by_col.setdefault(col, []).append(r)
        return {"table_fqn": full_name, "columns": by_col}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def upsert_dq_rule(request: Request, rule: DQRuleIn):
    """Add or update a DQ rule. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to modify DQ rules.")
    if not _FULL_NAME_RE.match(rule.table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn")
    # A1 FIX: Validate expression at write time to prevent stored injection
    _validate_expression(rule.expression, rule.rule_type)
    safe = lambda s: (s or "").replace("'", "")
    import hashlib
    rule_id = safe(rule.rule_id or hashlib.sha256(
        f"{rule.table_fqn}{rule.column_name}{rule.expression}".encode()
    ).hexdigest()[:12])
    try:
        _ensure_dq_table()
        _execute_sql(
            f"INSERT OVERWRITE {DQ_TABLE} "
            f"SELECT rule_id, table_fqn, column_name, rule_type, expression, severity, "
            f"       created_by, created_at, updated_at, notes "
            f"FROM {DQ_TABLE} WHERE rule_id != '{rule_id}' "
            f"UNION ALL "
            f"SELECT '{rule_id}', '{safe(rule.table_fqn)}', "
            f"'{safe(rule.column_name)}', '{safe(rule.rule_type)}', "
            f"'{safe(rule.expression)}', '{safe(rule.severity)}', "
            f"'{safe(email)}', current_timestamp(), current_timestamp(), '{safe(rule.notes)}'"
        )
        return {"rule_id": rule_id, "status": "upserted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{rule_id}")
async def delete_dq_rule(request: Request, rule_id: str):
    """Delete a DQ rule by ID. Admin-gated."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    safe_id = rule_id.replace("'", "")[:64]
    try:
        _ensure_dq_table()
        _execute_sql(
            f"DELETE FROM {DQ_TABLE} WHERE rule_id = '{safe_id}'"
        )
        return {"rule_id": safe_id, "status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# DQ Live Metrics — execute rules and return pass/fail rates
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def dq_live_metrics(
    request: Request,
    table_fqn: str = Query(...),
    sample_size: int = Query(10000, ge=100, le=1000000),
):
    """Execute all DQ rules for a table against a sample and return live pass/fail rates.

    Returns per-rule pass rate + an overall quality score.
    """
    if not _FULL_NAME_RE.match(table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn")
    try:
        _ensure_dq_table()
        # Get rules for this table
        rules = _execute_sql(
            f"SELECT * FROM {DQ_TABLE} WHERE table_fqn = '{table_fqn}' ORDER BY column_name"
        )
        if not rules:
            return {"table_fqn": table_fqn, "metrics": [], "quality_score": None, "note": "No DQ rules defined"}

        metrics = []
        total_pass_rate = 0.0
        evaluated_count = 0

        for rule in rules:
            expression = rule.get("expression", "")
            column = rule.get("column_name", "")
            rule_type = rule.get("rule_type", "CUSTOM")
            rule_id = rule.get("rule_id", "")

            # Build the check SQL based on rule type
            check_sql = _build_check_sql(table_fqn, column, rule_type, expression, sample_size)
            if not check_sql:
                metrics.append({
                    "rule_id": rule_id, "column": column, "rule_type": rule_type,
                    "pass_rate": None, "status": "skipped", "error": "Cannot build check SQL"
                })
                continue

            try:
                result = _execute_sql(check_sql)
                if result:
                    total_rows = int(result[0].get("total_rows", 0) or 0)
                    passing_rows = int(result[0].get("passing_rows", 0) or 0)
                    pass_rate = (passing_rows / total_rows) if total_rows > 0 else 0.0

                    status = "pass" if pass_rate >= 0.99 else "warn" if pass_rate >= 0.9 else "fail"
                    metrics.append({
                        "rule_id": rule_id, "column": column, "rule_type": rule_type,
                        "pass_rate": round(pass_rate, 4), "total_rows": total_rows,
                        "passing_rows": passing_rows, "failing_rows": total_rows - passing_rows,
                        "status": status, "severity": rule.get("severity", "ERROR"),
                    })
                    total_pass_rate += pass_rate
                    evaluated_count += 1
                else:
                    metrics.append({
                        "rule_id": rule_id, "column": column, "rule_type": rule_type,
                        "pass_rate": None, "status": "no_data"
                    })
            except Exception as rule_err:
                metrics.append({
                    "rule_id": rule_id, "column": column, "rule_type": rule_type,
                    "pass_rate": None, "status": "error", "error": str(rule_err)[:200]
                })

        quality_score = round(total_pass_rate / evaluated_count, 4) if evaluated_count > 0 else None

        return {
            "table_fqn": table_fqn,
            "metrics": metrics,
            "quality_score": quality_score,
            "quality_grade": _score_to_grade(quality_score),
            "rules_evaluated": evaluated_count,
            "rules_total": len(rules),
            "sample_size": sample_size,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/propagation")
async def dq_quality_propagation(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Show quality score propagation: how upstream DQ issues affect this table.

    Queries upstream tables' quality scores and computes a weighted propagated score.
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    table_fqn = f"{c}.{s}.{t}"

    try:
        _ensure_dq_table()
        # Get upstream tables from lineage
        upstream = _execute_sql(f"""
            SELECT DISTINCT source_table_full_name
            FROM system.access.table_lineage
            WHERE target_table_catalog = '{c}'
              AND target_table_schema = '{s}'
              AND target_table_name = '{t}'
              AND event_time > current_timestamp() - INTERVAL 90 DAYS
            LIMIT 20
        """)

        propagation = []
        for row in upstream:
            upstream_fqn = row.get("source_table_full_name", "")
            if not upstream_fqn:
                continue
            # Check if upstream has DQ rules
            upstream_rules = _execute_sql(
                f"SELECT COUNT(*) as cnt FROM {DQ_TABLE} WHERE table_fqn = '{upstream_fqn.replace(chr(39), chr(39)*2)}'"
            )
            rule_count = int(upstream_rules[0]["cnt"]) if upstream_rules else 0
            propagation.append({
                "upstream_table": upstream_fqn,
                "has_dq_rules": rule_count > 0,
                "rule_count": rule_count,
            })

        return {
            "table_fqn": table_fqn,
            "upstream_quality": propagation,
            "upstream_count": len(propagation),
            "covered_count": sum(1 for p in propagation if p["has_dq_rules"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# A1 FIX: Blocklist of SQL injection patterns in CUSTOM/RANGE expressions.
# These are patterns that should NEVER appear in a legitimate DQ expression.
import re
_INJECTION_PATTERNS = re.compile(
    r"(;\s*DROP|;\s*DELETE|;\s*INSERT|;\s*UPDATE|;\s*ALTER|;\s*CREATE|;\s*EXEC|"
    r"UNION\s+ALL\s+SELECT|UNION\s+SELECT|INTO\s+OUTFILE|LOAD_FILE|"
    r"xp_cmdshell|information_schema|pg_catalog|sys\.dm_|"
    r"/\*.*\*/|--\s|;\s*GRANT|;\s*REVOKE)",
    re.IGNORECASE
)

# A1 FIX: Maximum expression length to prevent payload smuggling
_MAX_EXPRESSION_LEN = 500


def _validate_expression(expression: str, rule_type: str) -> str:
    """A1 FIX: Validate DQ expression is safe before execution.

    For CUSTOM rules, this is critical — the expression is interpolated
    directly into SQL. We reject expressions containing known injection
    patterns and enforce length limits.
    """
    if not expression:
        return expression
    if len(expression) > _MAX_EXPRESSION_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Expression too long ({len(expression)} chars, max {_MAX_EXPRESSION_LEN})"
        )
    if _INJECTION_PATTERNS.search(expression):
        raise HTTPException(
            status_code=400,
            detail="Expression contains disallowed SQL patterns (possible injection)"
        )
    # Reject unbalanced parentheses (common injection vector)
    if expression.count("(") != expression.count(")"):
        raise HTTPException(
            status_code=400,
            detail="Expression has unbalanced parentheses"
        )
    return expression


def _build_check_sql(table_fqn: str, column: str, rule_type: str, expression: str, sample_size: int) -> str | None:
    """Build a SQL query to evaluate a DQ rule and return total/passing row counts.

    A1 FIX: Expressions are validated against injection patterns before use.
    """
    safe_col = f"`{column}`" if column else "*"
    safe_tbl = table_fqn  # Already validated via _FULL_NAME_RE

    if rule_type == "NOT_NULL" and column:
        return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN {safe_col} IS NOT NULL THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    elif rule_type == "UNIQUE" and column:
        return f"SELECT COUNT(*) AS total_rows, COUNT(DISTINCT {safe_col}) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    elif rule_type == "RANGE" and expression:
        # A1 FIX: Validate range expression
        _validate_expression(expression, "RANGE")
        # expression expected format: "min_val,max_val" — values must be numeric
        parts = expression.split(",")
        if len(parts) == 2:
            try:
                min_val = float(parts[0].strip())
                max_val = float(parts[1].strip())
            except ValueError:
                return None  # Non-numeric range values — skip rather than inject
            return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN {safe_col} BETWEEN {min_val} AND {max_val} THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    elif rule_type == "REGEX" and expression and column:
        # A1 FIX: Validate regex expression
        _validate_expression(expression, "REGEX")
        safe_expr = expression.replace("'", "''")
        return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN {safe_col} RLIKE '{safe_expr}' THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    elif rule_type == "CUSTOM" and expression:
        # A1 FIX: Validate CUSTOM expression (most dangerous — executed directly)
        _validate_expression(expression, "CUSTOM")
        safe_expr = expression.replace("'", "''")
        return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN ({safe_expr}) THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    return None


def _score_to_grade(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 0.99:
        return "A"
    elif score >= 0.95:
        return "B"
    elif score >= 0.9:
        return "C"
    elif score >= 0.8:
        return "D"
    return "F"
