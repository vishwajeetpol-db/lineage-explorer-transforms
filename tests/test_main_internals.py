"""Coverage for backend.main internal helpers: _get_user_info (token path,
cache, failure), _safe_error, _validate_identifier, and /health."""
from unittest.mock import patch, MagicMock

import backend.main as m


class TestGetUserInfo:
    def _req(self, token=None):
        req = MagicMock()
        req.headers = {"x-forwarded-access-token": token} if token else {}
        return req

    def test_no_token_no_localdev(self):
        with patch.dict("os.environ", {}, clear=False):
            m.os.environ.pop("LOCAL_DEV_ADMIN_EMAIL", None)
            email, is_admin = m._get_user_info(self._req())
        assert email is None and is_admin is False

    def test_localdev_admin_when_not_deployed(self):
        with patch.dict("os.environ", {"LOCAL_DEV_ADMIN_EMAIL": "dev@x.com"}, clear=False):
            m.os.environ.pop("DATABRICKS_APP_NAME", None)
            email, is_admin = m._get_user_info(self._req())
        assert email == "dev@x.com" and is_admin is True

    def test_localdev_blocked_on_deployed_app(self):
        with patch.dict("os.environ", {"LOCAL_DEV_ADMIN_EMAIL": "dev@x.com",
                                       "DATABRICKS_APP_NAME": "bricktrace-dev"}, clear=False):
            email, is_admin = m._get_user_info(self._req())
        assert email is None and is_admin is False

    def test_token_client_error_path(self):
        # Exercise the try/except resolve path (lines 184-211) without any real
        # SDK/network call: make _get_client raise so the except-branch runs and
        # caches (None, False).
        m._user_info_cache.clear()
        with patch.object(m, "_get_client", side_effect=RuntimeError("no client")):
            email, is_admin = m._get_user_info(self._req("tok-err"))
        assert email is None and is_admin is False
        # A second call hits the cached failure entry (line 177-182).
        with patch.object(m, "_get_client", side_effect=AssertionError("must not be called")):
            email2, _ = m._get_user_info(self._req("tok-err"))
        assert email2 is None

    def test_token_cache_hit(self):
        m._user_info_cache.clear()
        import time, hashlib
        h = hashlib.sha256("tok-cached".encode()).hexdigest()[:16]
        m._user_info_cache[h] = (time.time(), "cached@x.com", False)
        email, is_admin = m._get_user_info(self._req("tok-cached"))
        assert email == "cached@x.com" and is_admin is False

class TestSafeError:
    def test_sql_failed_message(self):
        assert "warehouse" in m._safe_error(RuntimeError("SQL failed: something")).lower()

    def test_no_warehouse_message(self):
        assert "warehouse" in m._safe_error(RuntimeError("No SQL warehouse available.")).lower()

    def test_long_message_truncated(self):
        out = m._safe_error(RuntimeError("x" * 500))
        assert out.endswith("...") and len(out) <= 210

    def test_short_message_passthrough(self):
        assert m._safe_error(ValueError("boom")) == "boom"


class TestValidateIdentifier:
    def test_ok(self):
        assert m._validate_identifier("  main  ", "catalog") == "main"

    def test_empty_raises(self):
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            m._validate_identifier("", "catalog")

    def test_injection_raises(self):
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            m._validate_identifier("bad;drop", "table")


class TestHealth:
    def test_health_ok(self, app_client):
        r = app_client.get("/health")
        assert r.status_code == 200
