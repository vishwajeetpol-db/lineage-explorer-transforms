"""Tests for backend/routes/notifications.py.

Covers: list, unread-count, mark-read, scan (with detection helpers),
rules CRUD, plus the module-level _execute_sql helper and _ensure_tables.
All SQL is mocked (patch the module's own _execute_sql / _get_client); offline.
The _tables_ensured global is reset per test so _lazy_ensure is deterministic.

Security regressions covered here (see the module's FIX comments):
  * /scan and both /rules writes are admin-gated  -> admin_client / 403 tests
  * rule_type + severity are allow-listed         -> 4xx, never reaches SQL
  * client-supplied rule_id must be a UUID        -> 400
  * every interpolated value goes through sql_str -> `\\'` cannot close a literal
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from databricks.sdk.service.sql import StatementState

# A payload that defeats plain quote-doubling on Databricks SQL: the leading
# backslash escapes the quote that doubling adds, so the *second* quote closes
# the literal and the rest runs as SQL. sql_str must double the backslash too.
BACKSLASH_INJECTION = "\\' || (SELECT concat_ws(',', collect_list(ssn)) FROM main.pii.customers) || '"
# What the escaped form must look like once embedded: `\\` then `''`.
ESCAPED_PREFIX = "\\\\''"


@pytest.fixture(autouse=True)
def _reset_ensured():
    """notifications caches a _tables_ensured flag; reset so each test sees a
    clean slate and _lazy_ensure calls are patched away."""
    import backend.routes.notifications as n
    n._tables_ensured = True  # skip CREATE TABLE side-effects by default
    yield
    n._tables_ensured = True


def _patch_sql(return_value=None, side_effect=None):
    if side_effect is not None:
        return patch("backend.routes.notifications._execute_sql", side_effect=side_effect)
    return patch("backend.routes.notifications._execute_sql",
                 return_value=return_value if return_value is not None else [])


# ---------------------------------------------------------------------------
# GET /api/notifications
# ---------------------------------------------------------------------------
class TestList:
    def test_list_default(self, app_client):
        rows = [{"notif_id": "1", "title": "x", "is_read": False}]
        with _patch_sql(return_value=rows):
            resp = app_client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["notifications"] == rows

    def test_list_with_filters(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications", params={
                "notif_type": "schema_change", "severity": "critical",
                "unread_only": True, "limit": 10})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "notif_type = 'schema_change'" in sql
        assert "severity = 'critical'" in sql
        assert "is_read = false" in sql
        assert "LIMIT 10" in sql

    def test_list_limit_out_of_bounds_422(self, app_client):
        resp = app_client.get("/api/notifications", params={"limit": 9999})
        assert resp.status_code == 422

    def test_list_sql_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications")
        assert resp.status_code == 500

    def test_list_error_detail_does_not_leak_sql(self, app_client):
        """500s must not echo the raw `SQL failed: ...` text back to the caller."""
        with _patch_sql(side_effect=RuntimeError("SQL failed: TABLE_OR_VIEW_NOT_FOUND secret_tbl")):
            resp = app_client.get("/api/notifications")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "SQL failed" not in detail
        assert "secret_tbl" not in detail

    def test_list_rejects_backslash_quote_notif_type(self, app_client):
        """FIX 2: the `\\'` bypass is refused by the allow-list before any SQL runs."""
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications",
                                  params={"notif_type": BACKSLASH_INJECTION})
        assert resp.status_code == 400
        assert "notif_type" in resp.json()["detail"]
        assert m.call_count == 0  # never reached the warehouse

    def test_list_rejects_backslash_quote_severity(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications",
                                  params={"severity": BACKSLASH_INJECTION})
        assert resp.status_code == 400
        assert "severity" in resp.json()["detail"]
        assert m.call_count == 0

    def test_list_rejects_unknown_notif_type(self, app_client):
        with _patch_sql(return_value=[]):
            resp = app_client.get("/api/notifications", params={"notif_type": "not_a_type"})
        assert resp.status_code == 400

    @pytest.mark.parametrize("value", ["schema_change", "dq_degradation",
                                       "sensitive_flow", "run_failure", "info"])
    def test_list_accepts_every_allow_listed_type(self, app_client, value):
        """Allow-list must cover everything _create_notification can store,
        including its "info" fallback — otherwise filtering breaks real rows."""
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications", params={"notif_type": value})
        assert resp.status_code == 200
        assert f"notif_type = '{value}'" in m.call_args[0][0]

    @pytest.mark.parametrize("value", ["info", "warning", "critical"])
    def test_list_accepts_every_allow_listed_severity(self, app_client, value):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications", params={"severity": value})
        assert resp.status_code == 200
        assert f"severity = '{value}'" in m.call_args[0][0]


# ---------------------------------------------------------------------------
# GET /api/notifications/unread-count
# ---------------------------------------------------------------------------
class TestUnreadCount:
    def test_count_ok(self, app_client):
        with _patch_sql(return_value=[{"cnt": 7}]):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 7

    def test_count_empty_rows(self, app_client):
        with _patch_sql(return_value=[]):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.json()["count"] == 0

    def test_count_error_returns_zero(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/notifications/mark-read
# ---------------------------------------------------------------------------
class TestMarkRead:
    def test_mark_all(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read", json={"all": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert "is_read = false" in m.call_args[0][0]

    def test_mark_specific_ids(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read",
                                   json={"notif_ids": ["a", "b'c"]})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "notif_id IN (" in sql
        assert "'b''c'" in sql  # single-quote escaped

    def test_mark_noop_when_empty(self, app_client):
        # no ids and all=False => returns ok without SQL error
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read", json={})
        assert resp.status_code == 200
        assert m.call_count == 0

    def test_mark_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/notifications/mark-read", json={"all": True})
        assert resp.status_code == 500

    def test_mark_read_stays_ungated_for_non_admin(self, non_admin_client):
        """Deliberately NOT admin-gated: it only flips a shared read flag, and
        gating it would break the notifications panel for every non-admin."""
        with _patch_sql(return_value=[]):
            resp = non_admin_client.post("/api/notifications/mark-read", json={"all": True})
        assert resp.status_code == 200

    def test_mark_read_escapes_backslash_quote_id(self, app_client):
        """notif_ids are caller-supplied, so they go through sql_str too."""
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read",
                                   json={"notif_ids": [BACKSLASH_INJECTION]})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert ESCAPED_PREFIX in sql          # backslash doubled, quote doubled
        assert "collect_list" not in sql.split("WHERE notif_id IN (")[0]


# ---------------------------------------------------------------------------
# POST /api/notifications/scan
# ---------------------------------------------------------------------------
class TestScan:
    def test_scan_detects_and_creates(self, admin_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes", return_value=[{"type": "schema_change"}]), \
             patch.object(n, "_detect_dq_degradation", return_value=[{"type": "dq_degradation"}]), \
             patch.object(n, "_detect_sensitive_flows", return_value=[{"type": "sensitive_flow"}]), \
             patch.object(n, "_create_notification") as mk:
            resp = admin_client.post("/api/notifications/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] == {
            "schema_changes": 1, "dq_degradation": 1, "sensitive_flows": 1}
        assert mk.call_count == 3

    def test_scan_error_500(self, admin_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/notifications/scan")
        assert resp.status_code == 500

    def test_scan_error_detail_does_not_leak_sql(self, admin_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes",
                          side_effect=RuntimeError("SQL failed: PERMISSION_DENIED on system.access")):
            resp = admin_client.post("/api/notifications/scan")
        assert resp.status_code == 500
        assert "SQL failed" not in resp.json()["detail"]

    def test_scan_requires_admin(self, non_admin_client):
        """FIX 3: documented as (admin) since day one but never enforced."""
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes") as det:
            resp = non_admin_client.post("/api/notifications/scan")
        assert resp.status_code == 403
        assert det.call_count == 0  # gate runs before any detection

    def test_scan_requires_admin_anonymous(self, app_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes") as det:
            resp = app_client.post("/api/notifications/scan")
        assert resp.status_code == 403
        assert det.call_count == 0


# ---------------------------------------------------------------------------
# run_scan dedup + background auto-scan loop
# ---------------------------------------------------------------------------
class TestRunScanAndAutoScan:
    def test_notif_key_uses_type_or_notif_type(self):
        import backend.routes.notifications as n
        assert n._notif_key({"type": "schema_change", "table_fqn": "c.s.t",
                              "column_name": "a", "title": "T"}) == ("schema_change", "c.s.t", "a", "T")
        # stored rows carry notif_type, not type
        assert n._notif_key({"notif_type": "sensitive_flow", "title": "X"}) == ("sensitive_flow", "", "", "X")

    def test_existing_keys_parses_rows(self):
        import backend.routes.notifications as n
        rows = [{"notif_type": "schema_change", "table_fqn": "c.s.t", "column_name": "a", "title": "T1"}]
        with _patch_sql(return_value=rows):
            keys = n._existing_notification_keys()
        assert ("schema_change", "c.s.t", "a", "T1") in keys

    def test_existing_keys_fail_open_on_error(self):
        import backend.routes.notifications as n
        with _patch_sql(side_effect=RuntimeError("no warehouse")):
            assert n._existing_notification_keys() == set()

    def test_run_scan_dedup_skips_existing(self):
        import backend.routes.notifications as n
        sc = [{"type": "schema_change", "table_fqn": "c.s.t", "column_name": "a", "title": "T1"}]
        sf = [{"type": "sensitive_flow", "table_fqn": "c.s.u", "column_name": "email", "title": "T2"}]
        created = []
        with patch.object(n, "_detect_schema_changes", return_value=sc), \
             patch.object(n, "_detect_dq_degradation", return_value=[]), \
             patch.object(n, "_detect_sensitive_flows", return_value=sf), \
             patch.object(n, "_existing_notification_keys", return_value={n._notif_key(sc[0])}), \
             patch.object(n, "_create_notification", side_effect=lambda it: created.append(it)):
            result = n.run_scan()
        assert result["detected"] == {"schema_changes": 1, "dq_degradation": 0, "sensitive_flows": 1}
        assert result["inserted"] == 1 and result["skipped"] == 1
        assert len(created) == 1 and created[0]["type"] == "sensitive_flow"

    def test_run_scan_inserts_all_when_none_existing(self):
        import backend.routes.notifications as n
        sc = [{"type": "schema_change", "title": "A"}, {"type": "schema_change", "title": "B"}]
        created = []
        with patch.object(n, "_detect_schema_changes", return_value=sc), \
             patch.object(n, "_detect_dq_degradation", return_value=[]), \
             patch.object(n, "_detect_sensitive_flows", return_value=[]), \
             patch.object(n, "_existing_notification_keys", return_value=set()), \
             patch.object(n, "_create_notification", side_effect=lambda it: created.append(it)):
            result = n.run_scan()
        assert result["inserted"] == 2 and result["skipped"] == 0
        assert len(created) == 2

    def test_autoscan_loop_runs_scan_then_retries_until_cancelled(self):
        import asyncio
        import backend.routes.notifications as n
        scans = []

        async def fake_sleep(_secs):
            # 1st call = initial delay; after a scan has run, stop the loop.
            if scans:
                raise asyncio.CancelledError()

        def fake_run():
            scans.append(1)
            return {"detected": {}, "inserted": 0, "skipped": 0}

        with patch.object(n, "run_scan", side_effect=fake_run), \
             patch.object(n.asyncio, "sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(n._autoscan_loop())
        assert len(scans) == 1  # ran exactly one scan, then the interval sleep cancelled


# ---------------------------------------------------------------------------
# GET/POST /api/notifications/rules  + DELETE
# ---------------------------------------------------------------------------
class TestRules:
    def test_list_rules(self, app_client):
        rows = [{"rule_id": "r1", "rule_type": "dq_degradation"}]
        with _patch_sql(return_value=rows):
            resp = app_client.get("/api/notifications/rules")
        assert resp.status_code == 200
        assert resp.json()["rules"] == rows

    def test_list_rules_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications/rules")
        assert resp.status_code == 500

    def test_create_rule_new_id(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert uuid.UUID(data["rule_id"])  # server-generated uuid
        assert "MERGE INTO" in m.call_args[0][0]

    def test_upsert_rule_existing_id(self, admin_client):
        rid = str(uuid.uuid4())
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_id": rid, "rule_type": "dq_degradation",
                "target_pattern": "main.*", "threshold": 0.8,
                "severity": "critical", "enabled": False, "notes": "o'brien"})
        assert resp.status_code == 200
        assert resp.json()["rule_id"] == rid
        sql = m.call_args[0][0]
        assert "o''brien" in sql       # escaped
        assert "enabled = false" in sql

    def test_create_rule_missing_type_422(self, admin_client):
        resp = admin_client.post("/api/notifications/rules", json={})
        assert resp.status_code == 422

    def test_upsert_rule_error_500(self, admin_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change"})
        assert resp.status_code == 500

    def test_upsert_rule_error_detail_does_not_leak_sql(self, admin_client):
        with _patch_sql(side_effect=RuntimeError("SQL failed: MERGE on lattice_lineage denied")):
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change"})
        assert resp.status_code == 500
        assert "SQL failed" not in resp.json()["detail"]

    def test_delete_rule(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 200
        assert "DELETE FROM" in m.call_args[0][0]

    def test_delete_rule_error_500(self, admin_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = admin_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 500

    def test_delete_rule_escapes_backslash_quote(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.delete(f"/api/notifications/rules/{BACKSLASH_INJECTION}")
        assert resp.status_code == 200
        assert ESCAPED_PREFIX in m.call_args[0][0]


# ---------------------------------------------------------------------------
# FIX 1/3: admin gate + rule_type/severity allow-list + rule_id UUID check
# ---------------------------------------------------------------------------
class TestRulesSecurity:
    def test_upsert_requires_admin(self, non_admin_client):
        """Peers dq.py / capability_closures.py both 403 non-admins here."""
        with _patch_sql(return_value=[]) as m:
            resp = non_admin_client.post("/api/notifications/rules",
                                         json={"rule_type": "schema_change"})
        assert resp.status_code == 403
        assert m.call_count == 0  # gate runs before any SQL

    def test_upsert_requires_admin_anonymous(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/rules",
                                   json={"rule_type": "schema_change"})
        assert resp.status_code == 403
        assert m.call_count == 0

    def test_delete_requires_admin(self, non_admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = non_admin_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 403
        assert m.call_count == 0

    def test_delete_requires_admin_anonymous(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 403
        assert m.call_count == 0

    def test_rule_type_injection_rejected(self, admin_client):
        """The confirmed exploit: rule_type carried a scalar subquery into the
        MATCHED branch. The Literal on AlertRuleIn refuses it before the handler
        body runs (FastAPI answers a body-schema violation with 422)."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_id": str(uuid.uuid4()),
                "rule_type": "x'||CAST((SELECT concat_ws(',', collect_list(ssn)) "
                             "FROM main.pii.customers) AS STRING)||'y"})
        assert resp.status_code == 422
        assert m.call_count == 0

    def test_unknown_rule_type_rejected(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "not_a_rule_type"})
        assert resp.status_code == 422
        assert m.call_count == 0

    def test_severity_injection_rejected(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_type": "schema_change", "severity": BACKSLASH_INJECTION})
        assert resp.status_code == 422
        assert m.call_count == 0

    @pytest.mark.parametrize("rule_type", ["schema_change", "dq_degradation",
                                          "sensitive_flow", "run_failure"])
    def test_documented_rule_types_still_accepted(self, admin_client, rule_type):
        """The Literal must not narrow the set the module already documents."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": rule_type})
        assert resp.status_code == 200
        assert f"rule_type = '{rule_type}'" in m.call_args[0][0]

    @pytest.mark.parametrize("severity", ["info", "warning", "critical"])
    def test_documented_severities_still_accepted(self, admin_client, severity):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change",
                                           "severity": severity})
        assert resp.status_code == 200
        assert f"severity = '{severity}'" in m.call_args[0][0]

    def test_defaults_preserved(self, admin_client):
        """Omitting the optional fields must still yield the documented defaults."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change"})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "target_pattern = '*'" in sql
        assert "threshold = 0.9" in sql
        assert "severity = 'warning'" in sql
        assert "enabled = true" in sql

    def test_non_uuid_rule_id_rejected(self, admin_client):
        """FIX 1b: rule_id is server-generated, so a supplied one must be a UUID."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_id": "pwn", "rule_type": "schema_change"})
        assert resp.status_code == 400
        assert "rule_id" in resp.json()["detail"]
        assert m.call_count == 0

    def test_injecting_rule_id_rejected(self, admin_client):
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_id": BACKSLASH_INJECTION, "rule_type": "schema_change"})
        assert resp.status_code == 400
        assert m.call_count == 0

    def test_target_pattern_and_notes_escape_backslash_quote(self, admin_client):
        """FIX 1c: these two were quote-doubled only, so `\\'` still closed the
        literal. Both must now come out with the backslash escaped as well."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_type": "schema_change",
                "target_pattern": BACKSLASH_INJECTION,
                "notes": BACKSLASH_INJECTION})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert sql.count(ESCAPED_PREFIX) >= 4  # pattern + notes, MATCHED + NOT MATCHED
        # No odd-length backslash run can survive to eat the doubling quote.
        assert "\\'" not in sql.replace(ESCAPED_PREFIX, "")

    def test_threshold_and_enabled_are_coerced(self, admin_client):
        """Non-numeric threshold / non-bool enabled cannot reach SQL text."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules", json={
                "rule_type": "schema_change", "threshold": "1=1; DROP TABLE x",
                "enabled": "'; DROP TABLE y --"})
        assert resp.status_code == 422
        assert m.call_count == 0

    def test_explicit_null_enabled_defaults_true(self, admin_client):
        """Optional[bool] admits None; it must not render as the literal `none`."""
        with _patch_sql(return_value=[]) as m:
            resp = admin_client.post("/api/notifications/rules",
                                     json={"rule_type": "schema_change", "enabled": None})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "enabled = true" in sql
        assert "= none" not in sql.lower()

    def test_list_rules_stays_readable_by_non_admin(self, non_admin_client):
        """Only the writes are gated; reading rules stays open like GET /."""
        with _patch_sql(return_value=[]):
            resp = non_admin_client.get("/api/notifications/rules")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Detection helpers (unit) + _create_notification + _ensure_tables/_lazy_ensure
# ---------------------------------------------------------------------------
class TestDetectionHelpers:
    def test_detect_schema_changes(self):
        import backend.routes.notifications as n
        rows = [{"table_catalog": "c", "table_schema": "s", "table_name": "t",
                 "column_name": "col", "data_type": "INT"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_schema_changes()
        assert out[0]["type"] == "schema_change"
        assert out[0]["table_fqn"] == "c.s.t"

    def test_detect_schema_changes_error_returns_empty(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("no perms")):
            assert n._detect_schema_changes() == []

    def test_detect_dq_degradation(self):
        import backend.routes.notifications as n
        rows = [{"rule_id": "r1", "rule_type": "not_null", "table_fqn": "c.s.t",
                 "column_name": "col"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_dq_degradation()
        assert out[0]["type"] == "dq_degradation"

    def test_detect_dq_degradation_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("x")):
            assert n._detect_dq_degradation() == []

    def test_detect_sensitive_flows(self):
        import backend.routes.notifications as n
        rows = [{"source_fqn": "c.s.a", "source_column_name": "email",
                 "target_fqn": "c.s.b", "target_column_name": "email"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_sensitive_flows()
        assert out[0]["severity"] == "critical"

    def test_detect_sensitive_flows_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("x")):
            assert n._detect_sensitive_flows() == []

    def test_create_notification(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", return_value=[]) as m:
            n._create_notification({"type": "schema_change", "title": "t'x",
                                    "detail": "d", "table_fqn": "c.s.t",
                                    "column_name": "col", "metadata": "{}"})
        assert "INSERT INTO" in m.call_args[0][0]
        assert "t''x" in m.call_args[0][0]

    def test_ensure_tables_creates(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", return_value=[]) as m:
            n._ensure_tables()
        assert m.call_count == 2  # notifications + rules tables

    def test_ensure_tables_swallows_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("denied")):
            n._ensure_tables()  # must not raise

    def test_lazy_ensure_runs_once(self):
        import backend.routes.notifications as n
        n._tables_ensured = False
        with patch.object(n, "_ensure_tables") as m:
            n._lazy_ensure()
            n._lazy_ensure()
        assert m.call_count == 1
        assert n._tables_ensured is True


# ---------------------------------------------------------------------------
# _execute_sql module helper
# ---------------------------------------------------------------------------
class TestExecuteSql:
    def _resp(self, state, rows=None, cols=None, err=None):
        resp = MagicMock()
        resp.status.state = state
        resp.status.error = MagicMock(message=err) if err else None
        if rows is None:
            resp.result = None
        else:
            resp.result.data_array = rows
            resp.manifest.schema.columns = [MagicMock() for _ in (cols or [])]
            for c, name in zip(resp.manifest.schema.columns, cols or []):
                c.name = name
        return resp

    def test_no_warehouse(self):
        import backend.routes.notifications as n
        with patch.object(n, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                n._execute_sql("SELECT 1")

    def test_success(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=[["a"]], cols=["x"])
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            assert n._execute_sql("SELECT 1") == [{"x": "a"}]

    def test_empty(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=None)
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            assert n._execute_sql("SELECT 1") == []

    def test_failed(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.FAILED, rows=None, err="bad")
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="SQL failed"):
                n._execute_sql("SELECT 1")
