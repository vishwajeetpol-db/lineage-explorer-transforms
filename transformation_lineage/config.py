"""Job configuration: Unity Catalog targets, KPI scope, credentials, tuning."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Sequence


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


@dataclass
class LineageJobConfig:
    """Runtime configuration for the daily lineage pipeline on Databricks."""

    # Unity Catalog location for managed Delta tables written by this job
    target_catalog: str
    target_schema: str

    # Fully-qualified table names (catalog.schema.table) that define KPI / critical scope
    kpi_tables: Sequence[str] = field(default_factory=tuple)

    # Look back window for system lineage discovery (hours)
    discovery_lookback_hours: int = 24

    # Version retention (days) — aligns with PRD default; pruning is optional follow-up
    history_retention_days: int = 90

    # Databricks workspace API (SDK). Usually same as workspace URL without path.
    databricks_host: str | None = None
    databricks_token: str | None = None

    # Optional: limit job runs processed per day (safety valve)
    max_runs_per_execution: int = 500

    # Git provider token for raw file fetch (e.g. GitHub fine-grained PAT). Optional if only workspace notebooks.
    git_http_token: str | None = None

    # Entity types for system.access.column_lineage. `NOTEBOOK` rows may not map to `jobs.get_run`.
    lineage_entity_types: tuple[str, ...] = ("JOB",)

    # Force a full re-parse: bypass content-version early-termination so even
    # byte-identical artifacts are re-parsed. Set by the app's force/clear flows
    # (a "Regenerate"/"clear & rebuild" must actually re-run the parser, not skip
    # it because the source content is unchanged).
    force_reparse: bool = False

    @classmethod
    def from_environment(
        cls,
        target_catalog: str,
        target_schema: str,
        kpi_tables: Sequence[str],
        **overrides: Any,
    ) -> LineageJobConfig:
        """Build config from environment variables with sensible names."""
        host = _env("DATABRICKS_HOST") or _env("LINEAGE_DATABRICKS_HOST")
        token = _env("DATABRICKS_TOKEN") or _env("LINEAGE_DATABRICKS_TOKEN")
        git_tok = _env("LINEAGE_GIT_HTTP_TOKEN") or _env("GITHUB_TOKEN")
        return cls(
            target_catalog=target_catalog,
            target_schema=target_schema,
            kpi_tables=tuple(kpi_tables),
            databricks_host=host,
            databricks_token=token,
            git_http_token=git_tok,
            **overrides,
        )

    @classmethod
    def from_dbutils(
        cls,
        dbutils: Any,
        spark: Any,
        target_catalog: str,
        target_schema: str,
        kpi_tables: Sequence[str],
        *,
        token_scope: str = "lineage",
        token_key: str = "databricks_pat",
        git_token_scope: str | None = "lineage",
        git_token_key: str | None = "git_http_token",
        **overrides: Any,
    ) -> LineageJobConfig:
        """Resolve host/token from Spark conf and dbutils.secrets (typical Databricks job pattern)."""
        host = spark.conf.get("spark.databricks.workspaceUrl", None)
        if host and not str(host).startswith("http"):
            host = f"https://{host}"
        token = None
        try:
            token = dbutils.secrets.get(scope=token_scope, key=token_key)
        except Exception:
            token = _env("DATABRICKS_TOKEN")
        git_tok = None
        if git_token_scope and git_token_key:
            try:
                git_tok = dbutils.secrets.get(scope=git_token_scope, key=git_token_key)
            except Exception:
                git_tok = _env("LINEAGE_GIT_HTTP_TOKEN")
        return cls(
            target_catalog=target_catalog,
            target_schema=target_schema,
            kpi_tables=tuple(kpi_tables),
            databricks_host=host,
            databricks_token=token,
            git_http_token=git_tok,
            **overrides,
        )

    def fully_qualified(self, table: str) -> str:
        return f"{self.target_catalog}.{self.target_schema}.{table}"
