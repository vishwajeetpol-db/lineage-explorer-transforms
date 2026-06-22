"""Fetch executed source text from workspace export or git HTTP APIs."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
import requests
from databricks.sdk import WorkspaceClient

from transformation_lineage.types import ResolvedTaskSource

logger = logging.getLogger(__name__)


def fetch_workspace_source(client: WorkspaceClient, path: str) -> str:
    """Export notebook or file from workspace (returns raw notebook JSON or file bytes decoded as utf-8)."""
    exp = client.workspace.export(path=path)
    raw = getattr(exp, "content", None)
    if raw is None:
        return ""
    if isinstance(raw, str):
        try:
            decoded = base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:
            decoded = raw
        return decoded
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _github_raw_url(git_url: str, commit: str, repo_path: str) -> str | None:
    """
    Build https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path} from git clone URL.
    """
    m = re.match(r"https?://github\.com/([^/]+)/([^/.]+)(?:\.git)?", git_url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    rp = repo_path.lstrip("/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{rp}"


def fetch_git_source_http(
    resolved: ResolvedTaskSource,
    *,
    http_token: str | None,
    timeout_sec: int = 60,
) -> str:
    """
    Fetch file at exact commit when git_url points to GitHub (extend for other providers).

    For non-GitHub URLs, returns empty string and caller should log / fallback.
    """
    if not resolved.git_url or not resolved.git_commit or not resolved.git_path:
        return ""
    url = _github_raw_url(resolved.git_url, resolved.git_commit, resolved.git_path)
    if not url:
        logger.warning("Git fetch not implemented for host: %s", resolved.git_url)
        return ""
    headers = {"Accept": "application/vnd.github.raw"}
    if http_token:
        headers["Authorization"] = f"Bearer {http_token}"
    resp = requests.get(url, headers=headers, timeout=timeout_sec)
    if resp.status_code != 200:
        logger.warning("GitHub raw fetch failed %s: %s", resp.status_code, url)
        return ""
    return resp.text


def fetch_resolved_source(
    client: WorkspaceClient,
    resolved: ResolvedTaskSource,
    *,
    git_http_token: str | None,
) -> tuple[str, str]:
    """
    Returns (raw_text, fetch_provenance_tag).
    """
    if resolved.source_kind == "workspace_notebook" and resolved.workspace_path:
        text = fetch_workspace_source(client, resolved.workspace_path)
        return text, f"workspace:{resolved.workspace_path}"
    if resolved.source_kind == "git_file":
        text = fetch_git_source_http(resolved, http_token=git_http_token)
        return text, f"git:{resolved.git_url}@{resolved.git_commit}:{resolved.git_path}"
    return "", "unresolved"
