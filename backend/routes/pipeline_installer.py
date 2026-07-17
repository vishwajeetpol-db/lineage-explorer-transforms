"""Automated pipeline capture installer — capability 29.

Admin-facing action that injects the %pip install cell and capture() call
into a target pipeline notebook. Requires CAN_MANAGE on the target job or
pipeline, and write access to the notebook.

Endpoints:
  POST /api/pipeline/install-capture    — inject capture cells into a notebook
  GET  /api/pipeline/install-capture/preview — dry-run: show what would be injected

Design notes (see docs/architecture.md §5):
  This capability was deferred because notebook-mutation logic is non-trivial
  and CAN_MANAGE on a live pipeline is a significant permission surface. This
  implementation uses the Databricks Workspace API to export/import the
  notebook, prepends two cells (pip-install + capture call), and re-imports.
  The operation is idempotent: if the capture cells are already present
  (detected by sentinel comment), nothing is changed.
"""
from __future__ import annotations

import os
import re
import base64
import logging
import textwrap
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from backend.lineage_service import _get_client
from backend.feature_flags import get_flag_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline-installer"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
_CAPTURE_SENTINEL = "# [BrickRoute] capture install sentinel"

_PIP_CELL_TEMPLATE = textwrap.dedent("""\
    {sentinel}
    # Auto-injected by BrickRoute pipeline installer (capability 29).
    # Remove both this cell and the capture() call cell to opt out.
    %pip install databricks-sdk --quiet
    # The capture module ships with the BrickRoute app; import it directly.
    import sys, os
    sys.path.insert(0, '/Workspace/Users')  # adjust if app path differs
    """).format(sentinel=_CAPTURE_SENTINEL)

_CAPTURE_CELL_TEMPLATE = textwrap.dedent("""\
    # [BrickRoute] capture() call — auto-injected.
    # Place this immediately BEFORE your pipeline write call.
    # capture() is non-fatal: it never raises and never performs the write itself.
    from backend.plan_capture import capture as _brickroute_capture
    _brickroute_capture(result_df, target="{target_table}")
    # Your existing write call follows unchanged.
    """)


def _notebook_has_capture(source: str) -> bool:
    return _CAPTURE_SENTINEL in source


def _prepend_capture_cells(source: str, target_table: str, language: str) -> str:
    """Prepend pip-install and capture cells to Python notebook source."""
    if language.upper() != "PYTHON":
        raise ValueError("Only Python notebooks are supported for automatic injection.")
    pip_cell = _PIP_CELL_TEMPLATE
    capture_cell = _CAPTURE_CELL_TEMPLATE.format(target_table=target_table)
    # For .py source notebooks, cells are delimited by # COMMAND ----------
    delimiter = "# COMMAND ----------"
    new_cells = f"{pip_cell}\n{delimiter}\n{capture_cell}\n{delimiter}\n"
    return new_cells + source


class InstallCaptureIn(BaseModel):
    notebook_path: str
    target_table: str
    dry_run: bool = False


@router.post("/install-capture")
async def install_capture(request: Request, body: InstallCaptureIn):
    """Inject capture cells into a pipeline notebook. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to modify pipeline notebooks.")
    if not get_flag_state("lineage_tracking.plan_capture"):
        raise HTTPException(status_code=409, detail="Runtime Plan Capture feature flag is disabled.")

    _FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")
    if not _FULL_NAME_RE.match(body.target_table):
        raise HTTPException(status_code=400, detail="Invalid target_table")

    notebook_path = body.notebook_path.strip()
    if not notebook_path.startswith("/"):
        raise HTTPException(status_code=400, detail="notebook_path must be an absolute workspace path")

    client = _get_client()
    try:
        export_resp = client.workspace.export(path=notebook_path, format="SOURCE")
        raw = base64.b64decode(export_resp.content or b"").decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not export notebook '{notebook_path}': {e}")

    if _notebook_has_capture(raw):
        return {
            "status": "already_installed",
            "notebook_path": notebook_path,
            "message": "Capture cells are already present in this notebook.",
        }

    try:
        modified = _prepend_capture_cells(raw, body.target_table, "PYTHON")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if body.dry_run:
        return {
            "status": "dry_run",
            "notebook_path": notebook_path,
            "preview_lines": modified.split("\n")[:40],
        }

    # Write back
    try:
        encoded = base64.b64encode(modified.encode("utf-8")).decode("ascii")
        client.workspace.import_(
            path=notebook_path,
            format="SOURCE",
            language="PYTHON",
            content=encoded,
            overwrite=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write notebook: {e}")

    logger.info(f"pipeline_installer: injected capture cells into {notebook_path} by {email}")
    return {
        "status": "installed",
        "notebook_path": notebook_path,
        "target_table": body.target_table,
        "installed_by": email,
    }


@router.get("/install-capture/preview")
async def preview_install(
    request: Request,
    notebook_path: str = Query(...),
    target_table: str = Query(...),
):
    """Dry-run: return what would be prepended without making any change."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    pip_cell = _PIP_CELL_TEMPLATE
    capture_cell = _CAPTURE_CELL_TEMPLATE.format(target_table=target_table)
    return {
        "pip_install_cell": pip_cell,
        "capture_call_cell": capture_cell,
        "sentinel": _CAPTURE_SENTINEL,
        "idempotent_check": "If sentinel is already in notebook, install is skipped.",
    }
