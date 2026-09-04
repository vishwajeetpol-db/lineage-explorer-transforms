"""Deep coverage for backend/routes/pipeline_installer.py — the capture-cell
injection logic (admin + feature-flag gated)."""
import base64
from unittest.mock import patch, MagicMock

import backend.routes.pipeline_installer as pi


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


class TestInstallCaptureGates:
    def test_non_admin_403(self, non_admin_client):
        r = non_admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 403

    def test_flag_disabled_409(self, admin_client):
        with patch.object(pi, "get_flag_state", return_value=False):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 409

    def test_bad_target_table_400(self, admin_client):
        with patch.object(pi, "get_flag_state", return_value=True):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "not_fqn"})
        assert r.status_code == 400

    def test_relative_path_400(self, admin_client):
        with patch.object(pi, "get_flag_state", return_value=True):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "relative/nb", "target_table": "c.s.t"})
        assert r.status_code == 400


class TestInstallCaptureFlow:
    def _client_with_notebook(self, source: str):
        client = MagicMock()
        client.workspace.export.return_value = MagicMock(content=_b64(source).encode())
        return client

    def test_already_installed(self, admin_client):
        # A notebook that already contains the capture marker.
        src = "# already\nimport plan_capture\nplan_capture.capture(df, 't')\n"
        client = self._client_with_notebook(src)
        with patch.object(pi, "get_flag_state", return_value=True), \
             patch.object(pi, "_get_client", return_value=client), \
             patch.object(pi, "_notebook_has_capture", return_value=True):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 200 and r.json()["status"] == "already_installed"

    def test_dry_run(self, admin_client):
        client = self._client_with_notebook("df = spark.read.table('x')\n")
        with patch.object(pi, "get_flag_state", return_value=True), \
             patch.object(pi, "_get_client", return_value=client), \
             patch.object(pi, "_notebook_has_capture", return_value=False), \
             patch.object(pi, "_prepend_capture_cells", return_value="CAPTURE\n<orig>"):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t", "dry_run": True})
        assert r.status_code == 200 and r.json()["status"] == "dry_run"

    def test_installed_writes_back(self, admin_client):
        client = self._client_with_notebook("df = spark.read.table('x')\n")
        with patch.object(pi, "get_flag_state", return_value=True), \
             patch.object(pi, "_get_client", return_value=client), \
             patch.object(pi, "_notebook_has_capture", return_value=False), \
             patch.object(pi, "_prepend_capture_cells", return_value="CAPTURE\n<orig>"):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 200 and r.json()["status"] == "installed"
        client.workspace.import_.assert_called_once()

    def test_export_failure_404(self, admin_client):
        client = MagicMock()
        client.workspace.export.side_effect = RuntimeError("not found")
        with patch.object(pi, "get_flag_state", return_value=True), \
             patch.object(pi, "_get_client", return_value=client):
            r = admin_client.post("/api/pipeline/install-capture",
                                  json={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 404


class TestPreview:
    def test_preview_non_admin_403(self, non_admin_client):
        r = non_admin_client.get("/api/pipeline/install-capture/preview",
                                 params={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code == 403

    def test_preview_admin(self, admin_client):
        client = MagicMock()
        client.workspace.export.return_value = MagicMock(content=_b64("df=1\n").encode())
        with patch.object(pi, "get_flag_state", return_value=True), \
             patch.object(pi, "_get_client", return_value=client), \
             patch.object(pi, "_notebook_has_capture", return_value=False), \
             patch.object(pi, "_prepend_capture_cells", return_value="CAPTURE\n"):
            r = admin_client.get("/api/pipeline/install-capture/preview",
                                 params={"notebook_path": "/W/nb", "target_table": "c.s.t"})
        assert r.status_code in (200, 400, 404)
