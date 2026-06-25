"""End-to-end extraction phase: discovery → resolve → fetch → normalize → persist-ready artifacts.

Cost optimizations:
  - Concurrent API calls via ThreadPoolExecutor (configurable parallelism)
  - Eliminates per-run sleep in favor of rate-limited thread pool
  - Path-keyed deduplication avoids redundant fetches
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession

from transformation_lineage.config import LineageJobConfig
from transformation_lineage.extraction.discovery import (
    discover_lineage_runs,
    distinct_notebook_entities,
    distinct_pipeline_entities,
    distinct_run_ids,
)
from transformation_lineage.extraction.fetcher import fetch_resolved_source, fetch_workspace_source
from transformation_lineage.extraction.normalize import cells_to_json, normalize_to_cells
from transformation_lineage.extraction.reporting import DailyExtractionReport, merge_skip_reason
from transformation_lineage.extraction.resolver import resolve_pipeline_libraries, resolve_run_tasks
from transformation_lineage.types import ExtractedArtifact

logger = logging.getLogger(__name__)

# Max parallel API calls (balances throughput vs. rate-limit risk)
_DEFAULT_CONCURRENCY = 16


def _stable_extraction_id(run_id: int, task_key: str | None, path_tag: str) -> str:
    """Path-keyed id so the same notebook discovered through multiple
    JOB/NOTEBOOK entity_runs collapses to ONE artifact (instead of N).
    """
    h = hashlib.sha256(path_tag.encode()).hexdigest()[:24]
    return f"ext_{h}"


def _default_language_for_path(path: str | None) -> str:
    if not path:
        return "python"
    pl = path.lower()
    if pl.endswith(".sql"):
        return "sql"
    return "python"


def _resolve_notebook_path(
    spark: SparkSession, notebook_id: str, lookback_days: int = 90
) -> str | None:
    """Resolve a single notebook entity_id to its workspace path via the audit log."""
    return _resolve_notebook_paths_batch(spark, [notebook_id], lookback_days).get(str(notebook_id))


def _scan_audit_for_paths(
    spark: SparkSession, ids: list[str], lookback_days: int
) -> dict[str, str | None]:
    """One grouped scan of system.access.audit to map notebookId -> path.

    Prunes on the `event_date` partition column; without a date predicate Spark
    scans the entire audit history (observed ~190s+).
    """
    found: dict[str, str | None] = {}
    if not ids:
        return found
    from pyspark.sql import functions as F

    df = (
        spark.table("system.access.audit")
        .where(F.col("event_date") >= F.date_sub(F.current_date(), lookback_days))
        .where(F.col("request_params").getItem("notebookId").isin(ids))
        .where(F.col("request_params").getItem("path").isNotNull())
        .groupBy(F.col("request_params").getItem("notebookId").alias("nb_id"))
        .agg(F.first(F.col("request_params").getItem("path"), ignorenulls=True).alias("nb_path"))
    )
    for row in df.collect():
        if row["nb_id"] is not None:
            found[str(row["nb_id"])] = row["nb_path"]
    return found


def _resolve_notebook_paths_batch(
    spark: SparkSession,
    notebook_ids: list[str],
    lookback_days: int = 90,
    cache_table: str | None = None,
) -> dict[str, str | None]:
    """Resolve notebook entity_ids -> workspace paths, audit-log scan cached in Delta.

    Resolution is expensive (a grouped scan of the large system.access.audit table).
    When `cache_table` is provided, previously-resolved ids are served from the cache
    and only cache-misses trigger the audit scan; new resolutions are written back so
    the cost is paid once per notebook across all pipeline runs.
    """
    result: dict[str, str | None] = {str(nid): None for nid in notebook_ids}
    if not notebook_ids:
        return result
    ids = [str(nid) for nid in notebook_ids]

    # 1) Serve hits from the cache.
    cached: dict[str, str | None] = {}
    if cache_table:
        try:
            from pyspark.sql import functions as F

            rows = (
                spark.table(cache_table)
                .where(F.col("notebook_id").isin(ids))
                .select("notebook_id", "nb_path")
                .collect()
            )
            cached = {str(r["notebook_id"]): r["nb_path"] for r in rows}
        except Exception as e:
            logger.warning("notebook path cache read failed: %s", e)
    result.update({k: v for k, v in cached.items() if k in result})

    # 2) Scan the audit log only for cache-misses.
    missing = [i for i in ids if i not in cached]
    if not missing:
        return result
    try:
        found = _scan_audit_for_paths(spark, missing, lookback_days)
    except Exception as e:
        logger.warning("Batch audit log lookup failed for %d notebooks: %s", len(missing), e)
        found = {}
    for k, v in found.items():
        if k in result:
            result[k] = v

    # 3) Write newly-resolved (non-null) paths back to the cache.
    if cache_table and found:
        try:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            new_rows = [
                {"notebook_id": k, "nb_path": v, "resolved_at": now}
                for k, v in found.items()
                if v
            ]
            if new_rows:
                (
                    spark.createDataFrame(new_rows)
                    .write.format("delta").mode("append").saveAsTable(cache_table)
                )
        except Exception as e:
            logger.warning("notebook path cache write failed: %s", e)
    return result


def _safe_int(value: str) -> int | None:
    """Safely convert string to int, returning None for non-numeric values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _process_single_run(
    client: WorkspaceClient,
    run_id: int,
    cfg: LineageJobConfig,
) -> list[ExtractedArtifact]:
    """Resolve + fetch + normalize a single job run. Thread-safe."""
    artifacts: list[ExtractedArtifact] = []
    try:
        tasks = resolve_run_tasks(client, run_id)
    except Exception as e:
        logger.warning("resolve_run_tasks failed run_id=%s: %s", run_id, e)
        return []

    if not tasks:
        return []

    for t in tasks:
        lang_hint = t.language or _default_language_for_path(t.workspace_path or t.git_path)
        if lang_hint not in ("sql", "python"):
            continue

        raw, prov = fetch_resolved_source(client, t, git_http_token=cfg.git_http_token)
        if not raw.strip():
            continue

        cells = normalize_to_cells(raw, lang_hint)
        norm_json = cells_to_json(cells)
        eid = _stable_extraction_id(run_id, t.task_key, prov)
        path = t.workspace_path or t.git_path
        artifacts.append(
            ExtractedArtifact(
                extraction_id=eid,
                run_id=run_id,
                job_id=t.job_id,
                task_key=t.task_key,
                source_kind=t.source_kind,
                source_path=path,
                git_commit=t.git_commit,
                raw_source=raw,
                normalized_cells_json=norm_json,
                language=lang_hint,
                extracted_at=datetime.now(timezone.utc),
            )
        )
    return artifacts


def _extract_object_definitions(
    spark: SparkSession, cfg: LineageJobConfig, report: DailyExtractionReport
) -> list[ExtractedArtifact]:
    """Definition-based extraction for declarative objects.

    For KPI targets that are VIEWs / MATERIALIZED VIEWs / STREAMING TABLEs, read
    the object's own ``SHOW CREATE TABLE`` DDL and treat it as a SQL artifact —
    bypassing discovery, producing-run resolution, and the system-table lineage
    lag entirely. This is how MV / streaming-table / SQL-defined-DLT column
    transformation lineage is captured.

    Tables whose source lives outside this metastore (Lakehouse Federation =
    table_type FOREIGN; Delta Sharing = a shared/foreign catalog) have NO local
    producing code, so they are recorded as an ``external_source`` skip reason
    instead of producing a (misleading) empty success.
    """
    artifacts: list[ExtractedArtifact] = []
    for fqn in cfg.kpi_tables:
        parts = fqn.split(".")
        if len(parts) != 3:
            continue
        cat, sch, name = (p.replace("`", "") for p in parts)
        ttype = None
        try:
            # Per-catalog information_schema (accessible with USE CATALOG), not
            # system.information_schema (which the app SP may lack access to).
            rows = spark.sql(
                f"SELECT table_type FROM `{cat}`.information_schema.tables "
                f"WHERE table_schema='{sch}' AND table_name='{name}'"
            ).collect()
            if rows:
                ttype = (rows[0]["table_type"] or "").upper()
        except Exception as e:  # noqa: BLE001
            logger.warning("information_schema lookup failed for %s: %s", fqn, e)

        if ttype == "FOREIGN":
            # Lakehouse Federation / Delta Sharing — producing logic is external.
            merge_skip_reason(report, "external_source")
            continue
        if ttype not in ("VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE"):
            # Plain managed/external table — no embedded definition; rely on the
            # discovery + job/notebook/pipeline resolution paths instead.
            continue

        try:
            ddl_rows = spark.sql(f"SHOW CREATE TABLE {fqn}").collect()
            ddl = "\n".join(r[0] for r in ddl_rows if r and r[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("SHOW CREATE TABLE failed for %s: %s", fqn, e)
            merge_skip_reason(report, "definition_unreadable")
            continue
        if not ddl.strip():
            continue

        cells = normalize_to_cells(ddl, "sql")
        norm_json = cells_to_json(cells)
        eid = _stable_extraction_id(0, None, f"definition:{fqn}")
        artifacts.append(
            ExtractedArtifact(
                extraction_id=eid,
                run_id=0,
                job_id=None,
                task_key=None,
                source_kind="object_definition",
                source_path=fqn,
                git_commit=None,
                raw_source=ddl,
                normalized_cells_json=norm_json,
                language="sql",
                extracted_at=datetime.now(timezone.utc),
            )
        )
        report.artifacts_extracted += 1
        report.by_source_kind["object_definition"] = (
            report.by_source_kind.get("object_definition", 0) + 1
        )
    if artifacts:
        logger.info("Definition-based extraction produced %d artifact(s)", len(artifacts))
    return artifacts


def run_extraction_phase(
    spark: SparkSession,
    cfg: LineageJobConfig,
    *,
    workspace_client_factory: Callable[[], WorkspaceClient] | None = None,
    max_concurrency: int = _DEFAULT_CONCURRENCY,
    notebook_path_cache_table: str | None = None,
) -> tuple[list[ExtractedArtifact], DailyExtractionReport]:
    """
    Discover runs from system lineage, fetch code per task, return artifacts + daily report.

    Uses ThreadPoolExecutor for parallel API calls (configurable via max_concurrency).
    """
    report = DailyExtractionReport(execution_ts=datetime.now(timezone.utc))
    if not cfg.databricks_host or not cfg.databricks_token:
        raise ValueError("databricks_host and databricks_token are required for extraction")

    def _client() -> WorkspaceClient:
        if workspace_client_factory:
            return workspace_client_factory()
        return WorkspaceClient(host=cfg.databricks_host, token=cfg.databricks_token)

    client = _client()

    _t = time.time()
    disc = discover_lineage_runs(
        spark,
        kpi_tables=cfg.kpi_tables,
        lookback_hours=cfg.discovery_lookback_hours,
        entity_types=cfg.lineage_entity_types,
    )
    report.runs_discovered = disc.select("entity_run_id").distinct().count()
    run_ids = distinct_run_ids(disc)
    report.timings["discover"] = round(time.time() - _t, 1)
    if cfg.max_runs_per_execution:
        run_ids = run_ids[: cfg.max_runs_per_execution]

    artifacts: list[ExtractedArtifact] = []

    # ── Definition-based extraction (views / MV / streaming tables) ──
    # Runs first and independently of discovery — covers declarative objects and
    # flags external (federated/shared) sources. No lineage lag.
    artifacts.extend(_extract_object_definitions(spark, cfg, report))

    # ── JOB entity extraction (CONCURRENT) ─────────────────────────
    logger.info("Processing %d job runs with concurrency=%d", len(run_ids), max_concurrency)
    report.runs_attempted += len(run_ids)

    _t = time.time()
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {
            pool.submit(_process_single_run, client, rid, cfg): rid
            for rid in run_ids
        }
        for future in as_completed(futures):
            rid = futures[future]
            try:
                run_artifacts = future.result()
                artifacts.extend(run_artifacts)
                for a in run_artifacts:
                    report.artifacts_extracted += 1
                    report.by_source_kind[a.source_kind] = (
                        report.by_source_kind.get(a.source_kind, 0) + 1
                    )
                if not run_artifacts:
                    merge_skip_reason(report, "no_resolvable_tasks")
            except Exception as e:
                logger.warning("Run %s failed: %s", rid, e)
                report.errors.append(f"resolve_run_tasks:{rid}:{e}")
                merge_skip_reason(report, "resolve_error")

    report.timings["job_fetch"] = round(time.time() - _t, 1)

    # ── NOTEBOOK entity extraction (CONCURRENT) ─────────────────────
    notebook_ids = distinct_notebook_entities(disc)
    logger.info("Processing %d notebook entities with concurrency=%d",
                len(notebook_ids), max_concurrency)

    # Pre-resolve all notebook paths (uses Spark, on driver). Cached in Delta so the
    # expensive audit-log scan is paid once per notebook; only cache-misses are scanned.
    _t = time.time()
    audit_lookback_days = max(1, (cfg.discovery_lookback_hours + 23) // 24) + 7
    nb_paths: dict[str, str | None] = _resolve_notebook_paths_batch(
        spark, list(notebook_ids),
        lookback_days=audit_lookback_days,
        cache_table=notebook_path_cache_table,
    )
    report.timings["notebook_resolve"] = round(time.time() - _t, 1)
    report.timings["notebook_count"] = len(notebook_ids)

    def _fetch_notebook(nb_id: str) -> ExtractedArtifact | None:
        nb_path = nb_paths.get(nb_id)
        if not nb_path:
            return None
        lang_hint = _default_language_for_path(nb_path)
        try:
            raw = fetch_workspace_source(client, nb_path)
        except Exception as e:
            logger.warning("fetch failed notebook=%s: %s", nb_path, e)
            return None
        if not raw.strip():
            return None
        cells = normalize_to_cells(raw, lang_hint)
        norm_json = cells_to_json(cells)
        numeric_id = _safe_int(nb_id)
        run_id_for_artifact = numeric_id if numeric_id is not None else hash(nb_id) & 0x7FFFFFFFFFFFFFFF
        eid = _stable_extraction_id(run_id_for_artifact, None, f"workspace:{nb_path}")
        return ExtractedArtifact(
            extraction_id=eid,
            run_id=run_id_for_artifact,
            job_id=None,
            task_key=None,
            source_kind="workspace_notebook",
            source_path=nb_path,
            git_commit=None,
            raw_source=raw,
            normalized_cells_json=norm_json,
            language=lang_hint,
            extracted_at=datetime.now(timezone.utc),
        )

    _t = time.time()
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(_fetch_notebook, nb_id): nb_id for nb_id in notebook_ids}
        for future in as_completed(futures):
            nb_id = futures[future]
            report.runs_attempted += 1
            try:
                result = future.result()
                if result:
                    artifacts.append(result)
                    report.artifacts_extracted += 1
                    report.by_source_kind["workspace_notebook"] = (
                        report.by_source_kind.get("workspace_notebook", 0) + 1
                    )
                else:
                    merge_skip_reason(report, "notebook_path_unresolved_or_empty")
            except Exception as e:
                report.errors.append(f"notebook_fetch:{nb_id}:{e}")
                merge_skip_reason(report, "notebook_fetch_error")
    report.timings["notebook_fetch"] = round(time.time() - _t, 1)

    # ── PIPELINE entity extraction (Lakeflow / DLT, CONCURRENT) ─────
    # Resolves each DLT pipeline to its source libraries and parses them. This
    # covers Python-defined DLT, whose logic is NOT in SHOW CREATE TABLE (so the
    # definition-based path above cannot see it).
    pipeline_ids = distinct_pipeline_entities(disc)
    if pipeline_ids:
        logger.info("Processing %d pipeline entities with concurrency=%d",
                    len(pipeline_ids), max_concurrency)

        def _fetch_pipeline(pid: str) -> list[ExtractedArtifact]:
            out: list[ExtractedArtifact] = []
            for src in resolve_pipeline_libraries(client, pid):
                lang = src.language or _default_language_for_path(src.workspace_path or src.git_path)
                if lang not in ("sql", "python"):
                    continue
                try:
                    raw, prov = fetch_resolved_source(client, src, git_http_token=cfg.git_http_token)
                except Exception as e:  # noqa: BLE001
                    logger.warning("pipeline library fetch failed pid=%s: %s", pid, e)
                    continue
                if not raw.strip():
                    continue
                cells = normalize_to_cells(raw, lang)
                eid = _stable_extraction_id(0, src.task_key, f"pipeline:{prov}")
                out.append(
                    ExtractedArtifact(
                        extraction_id=eid,
                        run_id=0,
                        job_id=None,
                        task_key=src.task_key,
                        source_kind="pipeline_library",
                        source_path=src.workspace_path or src.git_path,
                        git_commit=src.git_commit,
                        raw_source=raw,
                        normalized_cells_json=cells_to_json(cells),
                        language=lang,
                        extracted_at=datetime.now(timezone.utc),
                    )
                )
            return out

        _t = time.time()
        with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
            futures = {pool.submit(_fetch_pipeline, pid): pid for pid in pipeline_ids}
            for future in as_completed(futures):
                pid = futures[future]
                report.runs_attempted += 1
                try:
                    res = future.result()
                    if res:
                        artifacts.extend(res)
                        for a in res:
                            report.artifacts_extracted += 1
                            report.by_source_kind[a.source_kind] = (
                                report.by_source_kind.get(a.source_kind, 0) + 1
                            )
                    else:
                        merge_skip_reason(report, "pipeline_no_libraries")
                except Exception as e:  # noqa: BLE001
                    report.errors.append(f"pipeline_fetch:{pid}:{e}")
                    merge_skip_reason(report, "pipeline_fetch_error")
        report.timings["pipeline_fetch"] = round(time.time() - _t, 1)

    # Dedupe by extraction_id
    seen: set[str] = set()
    deduped: list[ExtractedArtifact] = []
    for a in artifacts:
        if a.extraction_id not in seen:
            seen.add(a.extraction_id)
            deduped.append(a)

    duplicates_dropped = len(artifacts) - len(deduped)
    if duplicates_dropped:
        logger.info("Deduped %d duplicate artifacts (kept %d unique)",
                    duplicates_dropped, len(deduped))
    return deduped, report


def new_extraction_id() -> str:
    """UUID when stable hash is not desired (tests)."""
    return f"ext_{uuid.uuid4().hex}"
