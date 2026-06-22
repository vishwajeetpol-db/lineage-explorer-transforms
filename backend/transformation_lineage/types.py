"""Shared datatypes for extraction, parsing, and lineage graph records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


SourceKind = Literal["workspace_notebook", "git_file", "unknown"]


@dataclass
class DiscoveredRun:
    workspace_id: str | None
    entity_type: str | None
    entity_id: str | None
    entity_run_id: str | None
    source_table_full_name: str | None
    target_table_full_name: str | None
    source_column_name: str | None
    target_column_name: str | None
    event_time: datetime | None


@dataclass
class ResolvedTaskSource:
    """One executable unit within a job run (typically one notebook task)."""

    run_id: int
    job_id: int | None
    task_key: str | None
    source_kind: SourceKind
    workspace_path: str | None = None
    git_url: str | None = None
    git_provider: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    git_path: str | None = None
    language: str | None = None


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


@dataclass
class ExtractedArtifact:
    """Normalized code artifact tied to a job run / task."""

    extraction_id: str
    run_id: int
    job_id: int | None
    task_key: str | None
    source_kind: SourceKind
    source_path: str | None
    git_commit: str | None
    raw_source: str
    normalized_cells_json: str
    language: str
    extracted_at: datetime = field(default_factory=_utcnow)
    skip_reason: str | None = None


@dataclass
class ParseResult:
    artifact_id: str
    language: str
    statements_parsed: int
    statements_skipped: int
    column_mappings: list[dict[str, Any]]
    table_references: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class LineageNodeRecord:
    node_id: str
    node_type: str
    label: str
    table_fqn: str | None
    column_name: str | None
    artifact_id: str | None
    meta_json: str


@dataclass
class LineageEdgeRecord:
    edge_id: str
    src_id: str
    dst_id: str
    edge_type: str
    artifact_id: str | None
    meta_json: str
