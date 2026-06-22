"""Orchestrator: schema → extract → version → parse → graph → reconcile → materialize.

Cost optimizations:
  - Batch version checking: single query checks all artifacts at once
  - Batch Delta writes: accumulates all nodes/edges and writes once per run
  - Early termination: skips parsing/graph/materialization when no new artifacts
  - Reduced collect() calls: uses pushdown predicates
  - PERF PATCH: 8-thread parallel parsing (sqlparse releases GIL)
  - PERF PATCH: Node deduplication before write
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from pyspark.sql import SparkSession

from transformation_lineage.config import LineageJobConfig
from transformation_lineage.extraction.pipeline import run_extraction_phase
from transformation_lineage.materialization.materializer import materialize_kpi_subgraphs
from transformation_lineage.parsing.artifact_parser import parse_artifact_cells
from transformation_lineage.parsing.graph_builder import build_graph_from_parse_results
from transformation_lineage.reconciliation.system_lineage import write_reconciliation_stats
from transformation_lineage.storage.schema import ensure_lineage_schema
from transformation_lineage.storage.writers import (
    write_extracted_artifacts,
    write_graph_records,
    write_json_report,
    write_parse_metrics_batch,
)
from transformation_lineage.sublineage.edge_endpoints_builder import build_edge_endpoints
from transformation_lineage.sublineage.expression_enricher import enrich_pyspark_expressions
from transformation_lineage.versioning.change_detection import (
    batch_check_versions,
    batch_record_versions,
    content_sha256,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _phase(name: str):
    """Log wall-clock time for a pipeline phase (prints so it shows in job output)."""
    t0 = time.time()
    print(f"[pipeline] \u25b6 {name} ...")
    try:
        yield
    finally:
        print(f"[pipeline] \u2714 {name} took {time.time() - t0:.1f}s")


def run_daily_pipeline(spark: SparkSession, cfg: LineageJobConfig) -> str:
    """
    Execute PRD requirements 1–6 (extraction through KPI graph materialization).

    Returns `pipeline_run_id` for traceability across Delta tables.
    """
    if not cfg.kpi_tables:
        raise ValueError("Configure at least one KPI table (catalog.schema.table).")

    pipeline_run_id = str(uuid.uuid4())
    tables = ensure_lineage_schema(spark, cfg)

    # ── Phase 1: Extraction ─────────────────────────────────────────
    logger.info("Starting extraction phase pipeline_run_id=%s", pipeline_run_id)
    with _phase("extraction (discover + fetch artifacts)"):
        artifacts, report = run_extraction_phase(
            spark, cfg, notebook_path_cache_table=tables.get("notebook_path_cache")
        )
        write_json_report(spark, tables["extraction_reports"], pipeline_run_id, report.to_dict())

    if not artifacts:
        logger.info("No artifacts extracted — skipping remaining phases.")
        return pipeline_run_id

    with _phase("write raw code + version check"):
        write_extracted_artifacts(
            spark,
            tables["raw_code"],
            pipeline_run_id,
            artifacts,
            content_sha256_fn=content_sha256,
        )

        # ── Phase 2: Batch version check (single query instead of N queries) ──
        sha_map = {a.extraction_id: content_sha256(a.raw_source) for a in artifacts}
        new_version_ids = batch_check_versions(spark, tables["code_versions"], sha_map)

        # Record all version rows in one batch write
        batch_record_versions(
            spark,
            tables["code_versions"],
            pipeline_run_id=pipeline_run_id,
            sha_map=sha_map,
            new_version_ids=new_version_ids,
        )

    # Early termination: no new content means no parsing needed
    new_artifacts = [a for a in artifacts if a.extraction_id in new_version_ids]
    if not new_artifacts:
        logger.info("All artifacts unchanged — skipping parse/graph/materialize.")
        return pipeline_run_id

    logger.info("Parsing %d new/changed artifacts (skipped %d unchanged)",
                len(new_artifacts), len(artifacts) - len(new_artifacts))

    # ── Phase 3: Parse + Graph (PARALLEL — threadpool releases GIL during sqlparse C calls) ──
    all_nodes = []
    all_edges = []
    parse_metrics: list[tuple[str, dict]] = []

    _PARSE_CONCURRENCY = min(len(new_artifacts), 8)  # 8 threads max (diminishing returns)

    def _parse_one(artifact):
        """Parse a single artifact and build its sub-graph. Thread-safe (no shared state)."""
        default_tbl = cfg.kpi_tables[0] if len(cfg.kpi_tables) == 1 else None
        parse = parse_artifact_cells(artifact.normalized_cells_json, artifact_id=artifact.extraction_id)
        nodes, edges = build_graph_from_parse_results(parse, default_table_fqn=default_tbl)
        return artifact.extraction_id, parse, nodes, edges

    with _phase(f"parse + build graph ({len(new_artifacts)} artifacts, concurrency={_PARSE_CONCURRENCY})"):
        with ThreadPoolExecutor(max_workers=_PARSE_CONCURRENCY) as pool:
            futures = {pool.submit(_parse_one, a): a for a in new_artifacts}
            for future in as_completed(futures):
                eid, parse, nodes, edges = future.result()
                parse_metrics.append((eid, parse))
                all_nodes.extend(nodes)
                all_edges.extend(edges)

    # ── Phase 4: Write (with node deduplication) ──
    with _phase("write parse metrics + graph records"):
        write_parse_metrics_batch(spark, tables["parse_metrics"], pipeline_run_id, parse_metrics)
        if all_nodes or all_edges:
            # Deduplicate nodes by node_id (keep latest)
            seen_node_ids = set()
            deduped_nodes = []
            for n in reversed(all_nodes):  # reversed so latest wins
                if n.node_id not in seen_node_ids:
                    seen_node_ids.add(n.node_id)
                    deduped_nodes.append(n)
            deduped_nodes.reverse()

            write_graph_records(
                spark, tables["nodes"], tables["edges"],
                pipeline_run_id, deduped_nodes, all_edges,
            )

    # ── Phase 5: Reconciliation ─────────────────────────────────────
    with _phase("reconciliation stats"):
        write_reconciliation_stats(
            spark,
            pipeline_run_id=pipeline_run_id,
            target_edges_table=tables["edges"],
            kpi_tables=list(cfg.kpi_tables),
            lookback_hours=cfg.discovery_lookback_hours,
            output_table=tables["reconciliation"],
        )

    # ── Phase 6: Materialization ──────────────────────────────────
    with _phase("materialize KPI subgraph cache"):
        materialize_kpi_subgraphs(
            spark,
            cfg,
            pipeline_run_id=pipeline_run_id,
            nodes_table=tables["nodes"],
            edges_table=tables["edges"],
            cache_table=tables["graph_cache"],
        )

    # ── Phase 7: Edge Endpoints ───────────────────────────────────
    with _phase("build edge endpoints"):
        endpoint_count = build_edge_endpoints(
            spark,
            pipeline_run_id=pipeline_run_id,
            nodes_table=tables["nodes"],
            edges_table=tables["edges"],
            raw_code_table=tables["raw_code"],
            endpoints_table=tables["edge_endpoints"],
        )
        logger.info("edge_endpoints rows=%d", endpoint_count)

    # ── Phase 8: Expression Enrichment (best-effort, non-blocking) ─────
    with _phase("expression enrichment (ai_query LLM)"):
        try:
            enrich_stats = enrich_pyspark_expressions(
                spark,
                pipeline_run_id=pipeline_run_id,
                endpoints_table=tables["edge_endpoints"],
                cache_table=tables["pyspark_to_sql_cache"],
            )
            logger.info("expression enrichment stats=%s", enrich_stats)
        except Exception as e:
            logger.warning("expression enrichment failed (continuing): %s", e)

    logger.info("Completed pipeline_run_id=%s", pipeline_run_id)
    return pipeline_run_id
