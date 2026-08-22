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
from backend.validators import require_admin, sql_str
from backend.circuit_breaker import sql_circuit_breaker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dq-rules", tags=["dq"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
DQ_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_rules"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_FULL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")
# Rule ids must round-trip between the write path and DELETE /{rule_id} unchanged.
# The write path used to accept any string while the delete path truncated to 64
# chars, so a rule stored under a longer id could never be deleted through the
# API — and its CUSTOM expression kept executing on every metrics call.
_RULE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


def _validate_column_name(value: str) -> str:
    """Validate a column name destined for a BACK-QUOTED SQL identifier.

    Deliberately NOT _validate/_IDENTIFIER_RE. Unity Catalog permits hyphens,
    spaces, dots and unicode in column names — which is precisely why the query
    builder back-quotes them — so screening against the bare-identifier regex
    refused real columns like `Order Date` or `my-col`, and marked existing rules
    on those columns permanently invalid.

    Inside a back-quoted identifier the only character that can end the quoting,
    and therefore the only one that can reach a code position, is the backtick
    itself. That is the actual security boundary, so that is what is rejected —
    together with control characters, for log and SQL hygiene. Everything else is
    inert once quoted.
    """
    v = (value or "").strip()
    if not v or len(v) > 255:
        raise HTTPException(
            status_code=400, detail=f"Invalid column_name: '{str(value)[:50]}'"
        )
    if "`" in v or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise HTTPException(
            status_code=400,
            detail="column_name may not contain a backtick or control characters",
        )
    return v


def _execute_sql(sql: str) -> list[dict]:
    """C8 FIX: Circuit breaker protects against warehouse timeout cascades."""
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available.")
    sql_circuit_breaker.check()
    client = _get_client()
    try:
        resp = client.statement_execution.execute_statement(
            statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
        )
        if resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error.message if resp.status.error else resp.status.state
            sql_circuit_breaker.record_failure()
            raise RuntimeError(f"SQL failed: {err}")
        sql_circuit_breaker.record_success()
        if not resp.result or not resp.result.data_array:
            return []
        columns = [c.name for c in resp.manifest.schema.columns]
        return [dict(zip(columns, row)) for row in resp.result.data_array]
    except RuntimeError:
        raise
    except Exception as e:
        sql_circuit_breaker.record_failure()
        raise RuntimeError(f"SQL failed: {e}")


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
    except Exception:
        logger.exception("dq: list_dq_rules failed")
        raise HTTPException(status_code=500, detail="Failed to list dq rules.")


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
    except Exception:
        logger.exception("dq: list_dq_rules_for_columns failed")
        raise HTTPException(status_code=500, detail="Failed to list dq rules for columns.")


@router.post("")
async def upsert_dq_rule(request: Request, rule: DQRuleIn):
    """Add or update a DQ rule. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to modify DQ rules.")
    if not _FULL_NAME_RE.match(rule.table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn")
    # column_name reaches a SQL code position in _build_check_sql (back-quoted),
    # so it needs allow-listing, not escaping. Validated here so a bad value is
    # rejected at write time rather than becoming an un-evaluatable stored rule.
    if rule.column_name:
        _validate_column_name(rule.column_name)
    # A1 FIX: Validate expression at write time to prevent stored injection
    _validate_expression(rule.expression, rule.rule_type)
    # A1 FIX: escape with the shared sql_str instead of stripping quotes. Quote
    # stripping silently mangled legitimate values and still left a break-out —
    # a value ending in a backslash escaped the closing quote of its own literal.
    safe = sql_str
    import hashlib
    rule_id = (rule.rule_id or hashlib.sha256(
        f"{rule.table_fqn}{rule.column_name}{rule.expression}".encode()
    ).hexdigest()[:12])
    if not _RULE_ID_RE.match(rule_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid rule_id: use up to 64 letters, digits, '_' or '-'",
        )
    try:
        _ensure_dq_table()
        _execute_sql(
            f"INSERT OVERWRITE {DQ_TABLE} "
            f"SELECT rule_id, table_fqn, column_name, rule_type, expression, severity, "
            f"       created_by, created_at, updated_at, notes "
            f"FROM {DQ_TABLE} WHERE rule_id != '{safe(rule_id)}' "
            f"UNION ALL "
            f"SELECT '{safe(rule_id)}', '{safe(rule.table_fqn)}', "
            f"'{safe(rule.column_name)}', '{safe(rule.rule_type)}', "
            f"'{safe(rule.expression)}', '{safe(rule.severity)}', "
            f"'{safe(email)}', current_timestamp(), current_timestamp(), '{safe(rule.notes)}'"
        )
        return {"rule_id": rule_id, "status": "upserted"}
    except Exception:
        logger.exception("dq: upsert_dq_rule failed")
        raise HTTPException(status_code=500, detail="Failed to upsert dq rule.")


@router.delete("/{rule_id}")
async def delete_dq_rule(request: Request, rule_id: str):
    """Delete a DQ rule by ID. Admin-gated."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    # Match the id EXACTLY as stored. Truncating here while the write path stored
    # the full value is what made over-length rules undeletable; ids are now
    # constrained at write time, so anything that fails this check cannot name a
    # stored rule and is a 400 rather than a silent no-op.
    if not _RULE_ID_RE.match(rule_id):
        raise HTTPException(status_code=400, detail="Invalid rule_id")
    try:
        _ensure_dq_table()
        existing = _execute_sql(
            f"SELECT 1 FROM {DQ_TABLE} WHERE rule_id = '{sql_str(rule_id)}' LIMIT 1"
        )
        if not existing:
            # The old handler reported {"status": "deleted"} whether or not a row
            # matched, so a typo'd id looked like a successful delete.
            raise HTTPException(status_code=404, detail=f"No DQ rule with id '{rule_id}'")
        _execute_sql(
            f"DELETE FROM {DQ_TABLE} WHERE rule_id = '{sql_str(rule_id)}'"
        )
        return {"rule_id": rule_id, "status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dq: failed to delete rule")
        raise HTTPException(status_code=500, detail="Failed to delete DQ rule.")


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

    Admin-gated: this endpoint EXECUTES stored CUSTOM expressions as the app
    service principal and returns row counts, so an ungated caller could read the
    result of any predicate an admin stored. The expression grammar (see
    _validate_expression) blocks subqueries, and this gate means only an admin can
    trigger the execution at all.

    UI consequence: DQMetricsPanel.tsx ("Run Checks", route `view=dq`) is reachable
    by any authenticated user and will now surface `403 Admin required` for
    non-admins — the panel/nav entry should be admin-only, like the Admin dashboard.
    """
    require_admin(request)
    if not _FULL_NAME_RE.match(table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn")
    # C10 FIX: Preflight privilege check — verify SELECT access before running metrics.
    # Without this, CUSTOM expressions silently fail when App SP lacks SELECT on the table.
    try:
        _execute_sql(f"SELECT 1 FROM {table_fqn} LIMIT 0")
    except Exception as priv_err:
        err_msg = str(priv_err)
        if "INSUFFICIENT_PERMISSIONS" in err_msg or "does not have" in err_msg or "ACCESS_DENIED" in err_msg:
            raise HTTPException(
                status_code=403,
                detail=f"App service principal lacks SELECT on {table_fqn}. "
                       "DQ metrics require row-level access. Grant SELECT or skip profiling."
            )
        # Other errors (warehouse, table not found) — let them fall through
        if "TABLE_OR_VIEW_NOT_FOUND" in err_msg or "does not exist" in err_msg:
            raise HTTPException(status_code=404, detail=f"Table not found: {table_fqn}")
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

            # Build the check SQL based on rule type. A rule stored before the
            # expression allow-list existed can still fail validation here — mark
            # that one rule invalid rather than 400-ing the whole panel, but never
            # execute it.
            try:
                check_sql = _build_check_sql(table_fqn, column, rule_type, expression, sample_size)
            except HTTPException as bad_expr:
                metrics.append({
                    "rule_id": rule_id, "column": column, "rule_type": rule_type,
                    "pass_rate": None, "status": "invalid",
                    "error": str(bad_expr.detail)[:200],
                })
                continue
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

        # A rule that could not be evaluated is NOT evidence of quality. Dividing
        # by evaluated_count alone means a check that stops running (invalid
        # expression, SQL error, no data) simply drops out of the average — so
        # breaking your strictest rule makes the grade go UP. Surface the shortfall
        # instead of hiding it: report the counts, and withhold the letter grade
        # when coverage is incomplete so a partial result can't be read as a pass.
        unevaluated = [
            m for m in metrics
            if m.get("status") in ("invalid", "skipped", "error", "no_data")
        ]
        coverage_complete = not unevaluated

        return {
            "table_fqn": table_fqn,
            "metrics": metrics,
            "quality_score": quality_score,
            # Only grade when every rule actually ran; otherwise the letter would
            # describe a subset the caller did not choose.
            "quality_grade": _score_to_grade(quality_score) if coverage_complete else None,
            "rules_evaluated": evaluated_count,
            "rules_total": len(rules),
            "rules_unevaluated": len(unevaluated),
            "coverage_complete": coverage_complete,
            "sample_size": sample_size,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("dq: dq_live_metrics failed")
        raise HTTPException(status_code=500, detail="Failed to dq live metrics.")


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
                f"SELECT COUNT(*) as cnt FROM {DQ_TABLE} WHERE table_fqn = '{sql_str(upstream_fqn)}'"
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
    except Exception:
        logger.exception("dq: dq_quality_propagation failed")
        raise HTTPException(status_code=500, detail="Failed to dq quality propagation.")


# A1 FIX: Expression validation is an ALLOW-list, not a deny-list.
#
# The previous deny-list (UNION SELECT / information_schema / `-- ` / `;DROP` …)
# could not work: a CUSTOM expression lands at SQL *code* position inside
# `SUM(CASE WHEN (<expr>) THEN 1 ELSE 0 END)`, and a bare scalar subquery needs
# none of the blocked tokens and no quotes at all —
#     (SELECT count(*) FROM main.hr.payroll WHERE salary > 250000) > 0
# passed every check, turning /api/dq-rules/metrics into a numeric oracle over
# any table the app service principal can read (iterate the bound → extract the
# value). The only sound defence at a code position is to accept a small,
# enumerated grammar and reject everything else.
import re

# A DQ expression is a boolean predicate over ONE row. It never legitimately
# names a table, opens a subquery, or terminates a statement, so these words are
# rejected as whole-word matches for every rule type.
_FORBIDDEN_WORDS = frozenset({
    "SELECT", "FROM", "JOIN", "WITH", "UNION", "INTERSECT", "EXCEPT", "EXISTS",
    "WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET", "INTO", "VALUES",
    "TABLE", "LATERAL", "OVER", "WINDOW", "PARTITION",
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL", "SET", "USE",
})

# Non-function keywords a predicate may use. Listed so that one of them sitting
# in front of a '(' — `AND (a > 1)`, `status IN ('a','b')` — is not mistaken for
# a call to a non-allow-listed function.
_OPERATOR_WORDS = frozenset({
    "AND", "OR", "NOT", "IS", "NULL", "IN", "BETWEEN", "LIKE", "ILIKE", "RLIKE",
    "REGEXP", "TRUE", "FALSE", "CASE", "WHEN", "THEN", "ELSE", "END", "AS", "DIV",
})

# Scalar functions a real column check may need. Deliberately small — anything
# not listed here is rejected rather than reasoned about.
_ALLOWED_FUNCTIONS = frozenset({
    "abs", "cast", "try_cast", "ceil", "ceiling", "char_length", "character_length",
    "coalesce", "concat", "date", "day", "floor", "length", "lower", "ltrim", "mod",
    "month", "nullif", "nvl", "regexp_like", "round", "rtrim", "sign", "substr",
    "substring", "to_date", "trim", "upper", "year",
})

# Statement/comment tokens that must not appear anywhere outside a string literal.
_FORBIDDEN_TOKENS = (";", "--", "/*", "*/")

# Every token a validated predicate may be built from. Each lexical form gets a
# NAMED group so the scanner can tell them apart afterwards: the previous version
# matched all of them with one anonymous alternation and then re-inspected the
# matched text with `token[:1].isalpha()`, which is False for a back-quoted
# identifier — so `` `reflect`(...) `` skipped the function allow-list entirely.
#
# String literals and back-quoted identifiers are matched here as SINGLE units,
# which is what makes one pass sufficient (see _tokenize).
_LEX_RE = re.compile(
    r"""(?P<ws>\s+)                                          # whitespace
      | (?P<num>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)     # numeric literal
      | (?P<str>'(?:[^']|'')*')                              # string literal ('' = escaped quote)
      | (?P<bident>`[^`]{1,255}`)                            # back-quoted identifier
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)                    # bare identifier / keyword
      | (?P<op><=|>=|<>|!=|=|<|>|\|\||[+\-*/%])              # comparison / concat / arithmetic
      | (?P<punct>[(),.])                                    # grouping, list, qualifier
    """,
    re.VERBOSE,
)

# A1 FIX: Maximum expression length to prevent payload smuggling
_MAX_EXPRESSION_LEN = 500

# Rule types whose `expression` never reaches a SQL code position: REGEX goes
# inside a quoted (and sql_str-escaped) literal, RANGE is parsed into two floats.
# Escaping is sound at a literal position and the value provably cannot become
# code, so these types get the length cap only. They deliberately do NOT get the
# keyword/comment screen: it bought nothing at a literal position while rejecting
# every legitimate regex containing `*/`, `--`, or a word like `select`.
_NON_CODE_RULE_TYPES = ("REGEX", "RANGE")

# Type names that may legitimately precede '(' inside a CAST — `DECIMAL(18,2)`,
# `VARCHAR(10)`. These are parameterised type constructors, not function calls.
_ALLOWED_TYPE_NAMES = frozenset({
    "decimal", "numeric", "dec", "varchar", "char", "string", "int", "integer",
    "bigint", "smallint", "tinyint", "double", "float", "real", "boolean",
    "date", "timestamp", "binary",
})


def _tokenize(expression: str) -> list[tuple[str, str]]:
    """Lex `expression` into (kind, text) pairs, rejecting anything unrecognised.

    ONE pass produces the token stream that every downstream check then runs on.
    That is the whole point: the previous implementation stripped string literals
    in one pass and re-scanned the *stripped* text in another, so validation and
    execution disagreed about where a literal began. A `'` inside a back-quoted
    identifier opened a phantom literal and blanked an arbitrary region —
    `` `a'` = 1 OR (SELECT 1 FROM main.hr.payroll) > 0 OR `b'` = 1 `` validated as
    three harmless tokens while the subquery was interpolated and executed
    verbatim. Matching literals and back-quoted identifiers as single units here
    makes that class of divergence unrepresentable.

    Whitespace tokens are kept in the stream so callers can faithfully
    reconstruct the code (see _code_only); filter them with _significant.
    """
    if "\\" in expression:
        raise HTTPException(
            status_code=400, detail="Expression may not contain backslashes"
        )
    tokens: list[tuple[str, str]] = []
    pos, n = 0, len(expression)
    while pos < n:
        m = _LEX_RE.match(expression, pos)
        if not m:
            ch = expression[pos]
            if ch == "'":
                raise HTTPException(
                    status_code=400,
                    detail="Expression has an unterminated string literal",
                )
            if ch == "`":
                raise HTTPException(
                    status_code=400,
                    detail="Expression has an unterminated quoted identifier",
                )
            raise HTTPException(
                status_code=400,
                detail=f"Expression contains a disallowed character: '{ch}'",
            )
        kind, text = m.lastgroup, m.group(0)
        pos = m.end()
        if kind == "bident":
            # A quoted identifier is emitted verbatim at code position. Spark
            # would read a contained quote as part of the name, but allowing one
            # leaves the reader unable to tell code from data at a glance, so
            # reject the characters that interact with SQL quoting outright. No
            # real column name needs them.
            inner = text[1:-1]
            if any(c in inner for c in "'\\;"):
                raise HTTPException(
                    status_code=400,
                    detail="Quoted identifier may not contain a quote, "
                           "backslash or semicolon",
                )
        tokens.append((kind, text))
    return tokens


def _significant(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop whitespace tokens."""
    return [(k, t) for k, t in tokens if k != "ws"]


def _code_only(tokens: list[tuple[str, str]]) -> str:
    """Reconstruct the expression with literals and quoted identifiers masked.

    Built from the authoritative token stream, so a substring search for comment
    and statement tokens can neither be fooled by their appearance inside a
    literal nor trip over a legitimate `a - -1`, whose operators are separated by
    whitespace that this reconstruction preserves.
    """
    return "".join(" " if k in ("str", "bident") else t for k, t in tokens)


def _validate_predicate(expression: str) -> None:
    """Allow-list check for an expression interpolated at SQL code position."""
    tokens = _tokenize(expression)
    code = _code_only(tokens)
    for tok in _FORBIDDEN_TOKENS:
        if tok in code:
            raise HTTPException(
                status_code=400, detail=f"Expression may not contain '{tok}'"
            )

    sig = _significant(tokens)
    depth = 0
    for kind, text in sig:
        if kind != "punct":
            continue
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth < 0:
                raise HTTPException(
                    status_code=400, detail="Expression has unbalanced parentheses"
                )
    if depth != 0:
        raise HTTPException(
            status_code=400, detail="Expression has unbalanced parentheses"
        )

    # Reserved words are checked on BARE identifiers only. Back-quoting is the
    # documented way to name a column `order` or `values`, and a quoted
    # identifier can never be parsed as a keyword — the old check scanned the
    # raw text with a word regex that ran straight through backticks, so the
    # escape hatch was dead for exactly the words it existed for.
    for kind, text in sig:
        if kind == "ident" and text.upper() in _FORBIDDEN_WORDS:
            raise HTTPException(
                status_code=400,
                detail=f"Expression may not contain the SQL keyword '{text}' "
                       "(subqueries and statements are not allowed)",
            )

    for i, (kind, text) in enumerate(sig):
        if kind not in ("ident", "bident"):
            continue
        nxt = sig[i + 1] if i + 1 < len(sig) else None
        if not (nxt and nxt[0] == "punct" and nxt[1] == "("):
            continue  # a column reference, not a call
        name = text[1:-1] if kind == "bident" else text
        # A dotted name resolves to a Unity Catalog function, NOT the builtin, so
        # an allow-listed final part proves nothing: anyone with CREATE FUNCTION
        # on a schema they own could define `mycat.mysch.abs` with a subquery body
        # and clear the entire grammar. Qualified calls are rejected outright.
        prev = sig[i - 1] if i else None
        if prev and prev[0] == "punct" and prev[1] == ".":
            raise HTTPException(
                status_code=400,
                detail=f"Expression may not call a qualified function: '{name}' "
                       "(only unqualified built-ins are allowed)",
            )
        if (name.lower() not in _ALLOWED_FUNCTIONS
                and name.lower() not in _ALLOWED_TYPE_NAMES
                and name.upper() not in _OPERATOR_WORDS):
            raise HTTPException(
                status_code=400,
                detail=f"Expression uses a function that is not allowed: '{name}'"
            )


def _validate_expression(expression: str, rule_type: str) -> str:
    """A1 FIX: Validate a DQ expression before it is stored or executed.

    CUSTOM (and any future code-position type) must satisfy the full allow-list
    grammar in _validate_predicate. REGEX/RANGE expressions are not SQL code and
    get the length cap only: a REGEX is sql_str-escaped inside a quoted literal
    and a RANGE is parsed into two floats before either reaches a statement.

    The keyword/comment screen that used to run here has been removed, because at
    a literal position it protected nothing while breaking real regexes. The
    escaping is the control, and it is sound — see
    TestBuildCheckSql.test_regex_literal_is_escaped, which pins that a `\\'`
    prefix cannot close the RLIKE literal. Screening keywords on top of that cost
    us every legitimate pattern containing `*/` (`^.*/v1/.*$`), `--`
    (`^[0-9]{4}--[0-9]{2}$`) or a bare SQL word (`^(select|update)$`), and no
    payload it rejected was reachable once the value is escaped.
    """
    if not expression:
        return expression
    if len(expression) > _MAX_EXPRESSION_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Expression too long ({len(expression)} chars, max {_MAX_EXPRESSION_LEN})"
        )
    if (rule_type or "").upper() in _NON_CODE_RULE_TYPES:
        return expression
    _validate_predicate(expression)
    return expression


def _build_check_sql(table_fqn: str, column: str, rule_type: str, expression: str, sample_size: int) -> str | None:
    """Build a SQL query to evaluate a DQ rule and return total/passing row counts.

    A1 FIX: Expressions are validated against injection patterns before use.

    `column` is validated here as well as at write time. It reaches a SQL *code*
    position via the back-quoted `safe_col` below, and back-quoting alone is not a
    control: an embedded backtick closes the identifier early, so a stored
    column_name of `` x`) THEN 1 ELSE 0 END) AS passing_rows, (SELECT …) AS leak
    FROM … -- `` rewrote the whole projection, and `dict(zip(columns, row))` let
    the injected duplicate alias win. Validating at read time too covers rules
    written before the write-time check existed.
    """
    if column:
        _validate_column_name(column)
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
        # A1 FIX: Validate regex expression. This one IS at literal position, so
        # it is escaped (backslash first, then quote — see validators.sql_str).
        _validate_expression(expression, "REGEX")
        safe_expr = sql_str(expression)
        return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN {safe_col} RLIKE '{safe_expr}' THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
    elif rule_type == "CUSTOM" and expression:
        # A1 FIX: A CUSTOM expression is SQL *code*, not a literal, so escaping is
        # not the control here — _validate_expression's allow-list grammar is.
        # Quote-doubling was actively wrong at this position: it provided no
        # protection (no enclosing quotes to close) while corrupting every
        # legitimate expression that contains a string literal, turning
        # `status = 'active'` into `status = ''active''`.
        _validate_expression(expression, "CUSTOM")
        return f"SELECT COUNT(*) AS total_rows, SUM(CASE WHEN ({expression}) THEN 1 ELSE 0 END) AS passing_rows FROM (SELECT * FROM {safe_tbl} LIMIT {sample_size})"
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
