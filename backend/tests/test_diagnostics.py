"""
run_diagnostics — deploy self-check. Verifies that missing prerequisites are
reported (not swallowed) and that required vs optional checks gate the overall
`ok` correctly. `_execute_sql` is patched to raise for chosen probes.
"""
from backend import lineage_service


def _patch(monkeypatch, fail_substrings=()):
    def fake_exec(client, sql, catalog=None):
        for s in fail_substrings:
            if s in sql:
                raise RuntimeError(f"permission denied: {s}")
        return [{"col": 1}]
    monkeypatch.setattr(lineage_service, "_get_client", lambda: object())
    monkeypatch.setattr(lineage_service, "_execute_sql", fake_exec)


class TestRunDiagnostics:
    def test_all_reachable(self, monkeypatch):
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh")
        _patch(monkeypatch)
        r = lineage_service.run_diagnostics()
        assert r["ok"] is True
        assert r["warehouse_id_set"] is True
        assert all(c["ok"] for c in r["checks"])

    def test_missing_warehouse_env_is_required_failure(self, monkeypatch):
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
        _patch(monkeypatch)
        r = lineage_service.run_diagnostics()
        assert r["ok"] is False
        assert r["warehouse_id_set"] is False
        assert r["checks"][0]["check"] == "warehouse" and not r["checks"][0]["ok"]

    def test_missing_system_access_fails_overall(self, monkeypatch):
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh")
        _patch(monkeypatch, fail_substrings=["system.access"])
        r = lineage_service.run_diagnostics()
        assert r["ok"] is False
        acc = next(c for c in r["checks"] if "system.access" in c["check"])
        assert not acc["ok"] and acc["required"] is True and acc.get("hint")

    def test_missing_billing_is_optional(self, monkeypatch):
        # billing is a non-required probe: it can fail without flipping overall ok
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh")
        _patch(monkeypatch, fail_substrings=["system.billing"])
        r = lineage_service.run_diagnostics()
        assert r["ok"] is True
        bill = next(c for c in r["checks"] if "system.billing" in c["check"])
        assert not bill["ok"] and bill["required"] is False
