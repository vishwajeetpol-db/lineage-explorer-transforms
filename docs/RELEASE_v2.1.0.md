# LineageForge v2.1.0 Release Notes

**Date**: 2025-06-22  
**Type**: Critical Integrity Patch  
**Status**: All 96 Python files pass AST syntax validation

---

## What Changed

### Critical Fixes

| File | Issue | Resolution |
|------|-------|------------|
| `parsing/graph_builder.py` | 0 bytes (empty) | Populated: 211 lines, transform category classification, canonical node IDs |
| `parsing/pyspark_ast_parser.py` | 0 bytes (empty) | Populated: 792 lines, full AST walker, multi-sink support, DataFrame alias resolution |
| `parsing/pyspark_parser.py` | 0 bytes (empty) | Populated: 207 lines, regex heuristic parser, spark.sql extraction, f-string expansion |
| `parsing/sql_parser.py` | 0 bytes (empty) | Populated: 334 lines, sqlparse-based, USE CATALOG/SCHEMA directives, per-stmt attribution |
| `pyspark_parser.py:26` | Syntax error | Fixed unterminated string literal in `_STRING_ASSIGN` regex pattern |

### Synced to Authoritative Source

| File | Delta |
|------|-------|
| `storage/schema.py` | +1498 bytes (12 Delta tables with liquid clustering) |
| `storage/writers.py` | +854 bytes (batch write with explicit Spark schemas) |
| `versioning/change_detection.py` | +691 bytes (batch SHA-256 version checking) |

---

## Validation Results

```
Content Check:  36/36 transformation_lineage library files have content
Syntax Check:   96/96 Python files pass ast.parse() validation
Self-contained: Zero references to lineage_builder or streamlit
Import Check:   All transformation_lineage.* imports resolve correctly
```

---

## Deployment Name & Branding

### Recommended Name: **LineageForge**

**Tagline**: *Column-level lineage, forged from source code*

**Rationale**:
- "Lineage" is the core domain
- "Forge" conveys both construction (building lineage graphs from raw code) and strength/precision
- Professional, memorable, deployment-friendly (no spaces, no special chars)
- Works as: `lineage-forge` (URL), `lineageforge` (package), `LineageForge` (display)

### Logo Concept

```
    ╱╲
   ╱  ╲
  ╱ ⚡ ╲    LineageForge
 ╱______╲
 │ ════ │   Column-level lineage,
 │ ════ │   forged from source code
 └──────┘
```

**Visual identity**:
- Primary color: `#FF4433` (ember red) - represents the forge
- Secondary: `#3B82F6` (graph blue) - represents lineage connections
- Dark mode canvas: `#0B0F19` (obsidian)
- Icon: Anvil silhouette with directed graph edges emerging from it
- Alternative icon: Molten metal flowing through a directed acyclic graph

### Bundle / App Registration

```yaml
# databricks.yml
bundle:
  name: lineage-forge

resources:
  apps:
    lineage-forge:
      name: LineageForge
      description: "Column-level transformation lineage explorer"
```

### App URL

Once deployed: `https://<workspace>.databricks.com/apps/lineage-forge`

---

## Full Feature Set (v2.1.0)

### Real-Time UC Lineage (lineage_service)
- Table-level upstream/downstream DAG
- Column-level lineage trace with BFS
- Parallel query execution (8-thread pool)
- Memory-bounded LRU cache with TTL
- Foreign catalog & Delta Sharing overlay
- Excel export

### Transformation Lineage (transform_service + pipeline)
- Deep column-level backtrack from any target column
- Transform category classification (aggregation, window, cast, arithmetic, etc.)
- BFS upstream traversal with pattern-based pruning
- Freshness-aware caching (separate TTL from main lineage)

### Build Management (build_service)
- One-click pipeline rebuild via serverless jobs
- Real-time 8-step progress polling
- KPI table targeting

### Pipeline Library (transformation_lineage/)
- 8-phase orchestrator with ThreadPoolExecutor parallelism
- AST-based PySpark parser (multi-sink, cross-cell symbol resolution)
- Regex-based PySpark fallback parser
- sqlparse-based SQL parser (per-statement, USE directive aware)
- Content versioning (SHA-256 skip-if-unchanged)
- 12 Delta table schema with liquid clustering
- LLM expression enrichment (PySpark to SQL via ai_query)
- KPI subgraph materialization with BFS expansion
- System lineage reconciliation

### Frontend (React + TypeScript)
- ReactFlow interactive DAG with ELK.js hierarchical layout
- Transform backtrack visualization
- Build progress panel
- Admin dashboard (cache stats, request metrics)
- Rate limiting awareness
