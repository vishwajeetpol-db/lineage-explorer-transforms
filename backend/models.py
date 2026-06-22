from pydantic import BaseModel
from typing import Literal, Optional, Union


class TableNode(BaseModel):
    node_type: Literal["table"] = "table"
    id: str
    name: str
    full_name: str
    table_type: str
    owner: Optional[str] = None
    comment: Optional[str] = None
    columns: list[dict] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    upstream_count: int = 0
    downstream_count: int = 0
    lineage_status: str = "connected"  # connected | root | leaf | orphan


class EntityNode(BaseModel):
    node_type: Literal["entity"] = "entity"
    id: str  # "entity:{type}:{id}"
    entity_type: str  # JOB, NOTEBOOK, PIPELINE, QUERY
    entity_id: str
    display_name: Optional[str] = None
    last_run: Optional[str] = None  # ISO timestamp of latest lineage event
    owner: Optional[str] = None
    cost_usd: Optional[float] = None  # 30-day serverless cost (list price). None = classic compute or no data.


class LineageEdge(BaseModel):
    source: str
    target: str


class ColumnLineageEdge(BaseModel):
    source_table: str
    source_column: str
    target_table: str
    target_column: str


class LineageResponse(BaseModel):
    nodes: list[Union[TableNode, EntityNode]]
    edges: list[LineageEdge]
    cached: bool = False
    cached_at: Optional[str] = None
    cache_expires_at: Optional[str] = None
    fetch_duration_ms: Optional[int] = None
    lineage_window_days: Optional[int] = None  # lookback window used for this graph
    truncated: bool = False  # True when a trace hit the node cap — graph is incomplete


class ColumnLineageResponse(BaseModel):
    edges: list[ColumnLineageEdge]


# ---------------------------------------------------------------------------
# Delta Sharing overlay — a lens layered on top of a lineage graph.
#
# This is NOT transform lineage: UC lineage stops at the metastore boundary.
# Instead we surface the *sharing relationships* recorded in
# system.information_schema (shares, recipients, providers, share usage):
#   - shared_out:        my tables that are published into a share (outbound)
#   - foreign_catalogs:  local catalogs created from a Delta Share (inbound)
# The frontend matches these against the nodes already in the graph and draws
# badges + synthetic boundary nodes/edges (table → share → recipient, and
# provider → shared-in table). Kept separate from LineageResponse so the
# toolbar toggle can lazy-load it without refetching the graph.
# ---------------------------------------------------------------------------


class SharedOutEntry(BaseModel):
    """A table of mine that is published into a Delta Share (provider side)."""
    full_name: str                       # catalog.schema.table — matches a graph node id
    share_name: str
    recipients: list[str] = []           # recipients granted SELECT on the share
    shared_as: Optional[str] = None      # alias the table is shared as, if renamed
    cdf_enabled: bool = False


class ForeignCatalogEntry(BaseModel):
    """A local catalog created from a Delta Share (recipient side)."""
    catalog_name: str                    # matches the catalog of shared-in graph nodes
    provider_name: str
    share_names: list[str] = []
    cloud: Optional[str] = None
    region: Optional[str] = None


class SharingOverlay(BaseModel):
    audience: str = "both"               # provider | recipient | both
    shared_out: list[SharedOutEntry] = []
    foreign_catalogs: list[ForeignCatalogEntry] = []
    available: bool = True               # False when the sharing views aren't readable


# ---------------------------------------------------------------------------
# Transformation Lineage Models — column-level expression-aware lineage
# from the LATTICE pipeline (transformation_lineage library).
#
# These power the "microscopic" drill-down: clicking a column on the table
# lineage graph opens the transformation sub-graph showing exactly how
# that column is derived (expressions, categories, source files).
# ---------------------------------------------------------------------------


class TransformNode(BaseModel):
    """A column node in the transformation graph."""
    node_id: str                          # e.g. "col:catalog.schema.table::column_name"
    table_fqn: str                        # fully qualified table name
    column: str                           # column name


class TransformEdge(BaseModel):
    """A transformation edge: source_column → target_column via an expression."""
    source_node_id: str
    target_node_id: str
    expression: str                       # the SQL/PySpark expression (e.g. "COALESCE(a, b)")
    category: str                         # ARITHMETIC, WINDOW, AGGREGATE, CAST, etc.
    category_color: str                   # hex color for the category
    source_file: str = ""                 # notebook/file where this transform is defined


class TransformLevel(BaseModel):
    """A depth layer in the backtracked transformation graph."""
    depth: int                            # 0 = target column, 1+ = upstream layers
    label: str                            # e.g. "Target Column", "Upstream Layer 1"
    color: str                            # hex color for this depth level
    nodes: list[TransformNode] = []       # columns discovered at this depth
    transforms: list[TransformEdge] = []  # edges flowing INTO this depth


class TransformResponse(BaseModel):
    """Full response for a transformation lineage trace."""
    levels: list[TransformLevel] = []
    has_lineage: bool = False
    is_source_column: bool = False        # True when the column has no upstream (it's a root)
    cached: bool = False
    cached_at: Optional[str] = None
    fetch_duration_ms: Optional[int] = None
    total_nodes: int = 0
    total_edges: int = 0
    max_depth_reached: int = 0


class FreshnessInfo(BaseModel):
    """Staleness status for a table's transformation lineage."""
    exists: bool = False
    edge_count: int = 0
    last_built: Optional[str] = None      # ISO timestamp of last materialization
    age_str: str = "Never built"          # human-readable age (e.g. "2h ago", "3d ago")
    is_stale: bool = True


class BuildJobRequest(BaseModel):
    """Request to trigger a transformation lineage build."""
    table_fqn: str                        # e.g. "catalog.schema.table"
    force_rebuild: bool = False           # bypass freshness check


class BuildJobStatus(BaseModel):
    """Status of a running/completed lineage build job."""
    run_id: str
    state: str                            # PENDING, RUNNING, TERMINATED, ERROR, etc.
    result_state: Optional[str] = None    # SUCCESS, FAILED, CANCELED
    state_message: str = ""
    progress_pct: int = 0                 # 0-100
    is_complete: bool = False
    is_success: bool = False
    current_step: int = 0                 # index into steps[]
    current_step_name: str = ""
    total_steps: int = 8
    steps: list[str] = []                 # ordered step names for progress bar
    run_page_url: str = ""                # link to the job run in Databricks UI
