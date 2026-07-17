"""Runtime Plan Capture — vendored from lineage-plan-capture (lineage_capture wheel).

Captures a DataFrame's Analyzed Logical Plan (works on classic Spark AND
serverless Spark Connect via `df.explain(mode="extended")`) into an app-owned
Delta table, then parses it into per-column {target_column, source_columns,
expression, confidence, notes} dicts.

Adapted for the combined app: DEFAULT_TABLE / DEFAULT_SPEC_TABLE now derive
from this app's LINEAGE_CATALOG / LINEAGE_SCHEMA env vars instead of the
plugin's original hardcoded `pritam_demo_workspace_catalog.lineage_explorer`
default (see capture.py). LINEAGE_CAPTURE_TABLE / LINEAGE_CDC_SPEC_TABLE still
override explicitly, unchanged from upstream.

This package is dormant by default — nothing here runs on its own. It is only
invoked from within a Lakeflow Job/Pipeline notebook that a user has
explicitly opted into (see docs/capabilites.md), and the app-side read path
(backend/plan_capture_service.py) only queries the resulting tables when the
`lineage_tracking.plan_capture` feature flag is enabled
(backend/feature_flags.py).
"""
from backend.plan_capture.capture import (
    capture,
    capture_cdc_spec,
    analyzed_plan,
    plan_hash,
    DEFAULT_TABLE,
    DEFAULT_SPEC_TABLE,
)
from backend.plan_capture.plan_parser import parse_plan

__all__ = [
    "capture",
    "capture_cdc_spec",
    "analyzed_plan",
    "plan_hash",
    "parse_plan",
    "DEFAULT_TABLE",
    "DEFAULT_SPEC_TABLE",
]
