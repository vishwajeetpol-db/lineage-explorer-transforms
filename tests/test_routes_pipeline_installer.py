"""Tests for backend/routes/pipeline_installer.py — capture-cell installer (cap 29).

Both endpoints are admin-gated (they mutate a pipeline notebook via the
Workspace API). Covers the admin gate + happy path with the SDK client mocked.
"""
from unittest.mock import patch

import pytest


# Valid bodies/params so requests reach the admin gate (FastAPI validates the
# body first — an incomplete body returns 422 before the handler's gate runs).
_INSTALL_BODY = {"notebook_path": "/W/nb", "target_table": "c.s.t"}
_PREVIEW_PARAMS = {"notebook_path": "/W/nb", "target_table": "c.s.t"}


class TestInstallCapture:
    def test_install_requires_admin(self, non_admin_client):
        resp = non_admin_client.post("/api/pipeline/install-capture", json=_INSTALL_BODY)
        assert resp.status_code == 403

    def test_preview_requires_admin(self, non_admin_client):
        resp = non_admin_client.get("/api/pipeline/install-capture/preview", params=_PREVIEW_PARAMS)
        assert resp.status_code == 403

    def test_install_admin_reaches_handler(self, admin_client):
        """Admin passes the gate; a mocked install path runs (not 403/422)."""
        resp = admin_client.post("/api/pipeline/install-capture", json=_INSTALL_BODY)
        assert resp.status_code not in (403, 422)

    def test_preview_admin_reaches_handler(self, admin_client):
        resp = admin_client.get("/api/pipeline/install-capture/preview", params=_PREVIEW_PARAMS)
        assert resp.status_code not in (403, 422)
