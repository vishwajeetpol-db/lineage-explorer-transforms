import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { api, setLiveMode, getLiveMode } from "./client";

// A fetch mock whose response is configurable per-test. Returns a Response-like
// object with ok/status/json/text as the client's fetchJson helper expects.
function makeFetch(payload: unknown, opts: { ok?: boolean; status?: number; text?: string } = {}) {
  const { ok = true, status = 200, text = "" } = opts;
  return vi.fn(async () => ({
    ok,
    status,
    json: async () => payload,
    text: async () => text,
  }));
}

// Convenience: last fetch call's [url, init].
function lastCall(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls[mock.mock.calls.length - 1];
}

describe("api/client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setLiveMode(false);
  });

  describe("live-mode toggling", () => {
    it("defaults to off and can be toggled", () => {
      expect(getLiveMode()).toBe(false);
      setLiveMode(true);
      expect(getLiveMode()).toBe(true);
      setLiveMode(false);
      expect(getLiveMode()).toBe(false);
    });

    it("appends &live=true when a query string is already present", async () => {
      const f = makeFetch({ status: "ok", version: "1", system_health: {} });
      vi.stubGlobal("fetch", f);
      setLiveMode(true);
      await api.getSchemas("cat");
      expect(lastCall(f)[0]).toBe("/api/schemas?catalog=cat&live=true");
    });

    it("appends ?live=true when no query string is present", async () => {
      const f = makeFetch({ tables: [] });
      vi.stubGlobal("fetch", f);
      setLiveMode(true);
      await api.getTables();
      expect(lastCall(f)[0]).toBe("/api/tables?live=true");
    });

    it("does not append live when off", async () => {
      const f = makeFetch({ tables: [] });
      vi.stubGlobal("fetch", f);
      await api.getTables();
      expect(lastCall(f)[0]).toBe("/api/tables");
    });
  });

  describe("fetchJson error path", () => {
    it("throws with status + body text when response not ok", async () => {
      const f = makeFetch(null, { ok: false, status: 500, text: "boom" });
      vi.stubGlobal("fetch", f);
      await expect(api.getTables()).rejects.toThrow("API error 500: boom");
    });
  });

  // GET helpers: assert URL, return value, and error branch.
  describe("GET methods", () => {
    it("getUserInfo", async () => {
      const payload = { email: "a@b.c", isAdmin: true };
      const f = makeFetch(payload);
      vi.stubGlobal("fetch", f);
      const out = await api.getUserInfo();
      expect(lastCall(f)[0]).toBe("/api/user-info");
      expect(out).toEqual(payload);
    });

    it("getImpact without optional params", async () => {
      const f = makeFetch({ table_full_name: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getImpact("c a", "s", "t");
      expect(lastCall(f)[0]).toBe("/api/impact?catalog=c%20a&schema=s&table=t");
    });

    it("getImpact with maxHops and refresh", async () => {
      const f = makeFetch({ table_full_name: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getImpact("c", "s", "t", 5, true);
      expect(lastCall(f)[0]).toBe("/api/impact?catalog=c&schema=s&table=t&max_hops=5&refresh=true");
    });

    it("getGovernance with refresh", async () => {
      const f = makeFetch({ table_full_name: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getGovernance("c", "s", "t", true);
      expect(lastCall(f)[0]).toBe("/api/governance?catalog=c&schema=s&table=t&refresh=true");
    });

    it("getGovernance without refresh", async () => {
      const f = makeFetch({ table_full_name: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getGovernance("c", "s", "t");
      expect(lastCall(f)[0]).toBe("/api/governance?catalog=c&schema=s&table=t");
    });

    it("listGovernanceRules", async () => {
      const f = makeFetch({ rules: [] });
      vi.stubGlobal("fetch", f);
      const out = await api.listGovernanceRules();
      expect(lastCall(f)[0]).toBe("/api/governance/config");
      expect(out).toEqual({ rules: [] });
    });

    it("getAccess with and without refresh", async () => {
      const f = makeFetch({ table_full_name: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getAccess("c", "s", "t", true);
      expect(lastCall(f)[0]).toBe("/api/access?catalog=c&schema=s&table=t&refresh=true");
      await api.getAccess("c", "s", "t");
      expect(lastCall(f)[0]).toBe("/api/access?catalog=c&schema=s&table=t");
    });

    it("getRootCauseTrace with and without optional params", async () => {
      const f = makeFetch({ focus_table: "c.s.t" });
      vi.stubGlobal("fetch", f);
      await api.getRootCauseTrace("c", "s", "t", 3, true);
      expect(lastCall(f)[0]).toBe("/api/root-cause/trace?catalog=c&schema=s&table=t&max_hops=3&refresh=true");
      await api.getRootCauseTrace("c", "s", "t");
      expect(lastCall(f)[0]).toBe("/api/root-cause/trace?catalog=c&schema=s&table=t");
    });

    it("getMlModelsForTable", async () => {
      const f = makeFetch({ models: [] });
      vi.stubGlobal("fetch", f);
      await api.getMlModelsForTable("c", "s", "t");
      expect(lastCall(f)[0]).toBe("/api/ml/models-for-table?catalog=c&schema=s&table=t");
    });

    it("getAnalyzeModels", async () => {
      const f = makeFetch({ models: ["m"], default: "m" });
      vi.stubGlobal("fetch", f);
      const out = await api.getAnalyzeModels();
      expect(lastCall(f)[0]).toBe("/api/analyze-producer/models");
      expect(out.default).toBe("m");
    });

    it("getAnalysisVersion", async () => {
      const f = makeFetch({ version: 2 });
      vi.stubGlobal("fetch", f);
      await api.getAnalysisVersion("JOB", "1", "c.s.t", 2);
      expect(lastCall(f)[0]).toBe(
        "/api/analyze-producer/version?entity_type=JOB&entity_id=1&target_table=c.s.t&version=2",
      );
    });

    it("compareAnalysisVersions", async () => {
      const f = makeFetch({ changed_count: 1 });
      vi.stubGlobal("fetch", f);
      await api.compareAnalysisVersions("JOB", "1", "c.s.t", 1, 2);
      expect(lastCall(f)[0]).toBe(
        "/api/analyze-producer/compare?entity_type=JOB&entity_id=1&target_table=c.s.t&from_version=1&to_version=2",
      );
    });

    it("getTables", async () => {
      const f = makeFetch({ tables: [] });
      vi.stubGlobal("fetch", f);
      await api.getTables();
      expect(lastCall(f)[0]).toBe("/api/tables");
    });

    it("getCatalogs", async () => {
      const f = makeFetch({ catalogs: [] });
      vi.stubGlobal("fetch", f);
      await api.getCatalogs();
      expect(lastCall(f)[0]).toBe("/api/catalogs");
    });

    it("getSchemas", async () => {
      const f = makeFetch({ schemas: [] });
      vi.stubGlobal("fetch", f);
      await api.getSchemas("c");
      expect(lastCall(f)[0]).toBe("/api/schemas?catalog=c");
    });

    it("getLineage passes signal", async () => {
      const f = makeFetch({ nodes: [], edges: [] });
      vi.stubGlobal("fetch", f);
      const ctrl = new AbortController();
      await api.getLineage("c", "s", ctrl.signal);
      expect(lastCall(f)[0]).toBe("/api/lineage?catalog=c&schema=s");
      expect(lastCall(f)[1]).toEqual({ signal: ctrl.signal });
    });

    it("getCatalogLineage", async () => {
      const f = makeFetch({ nodes: [], edges: [] });
      vi.stubGlobal("fetch", f);
      await api.getCatalogLineage("c");
      expect(lastCall(f)[0]).toBe("/api/lineage?catalog=c");
    });

    it("getLineageTrace", async () => {
      const f = makeFetch({ nodes: [], edges: [] });
      vi.stubGlobal("fetch", f);
      await api.getLineageTrace("c.s.t");
      expect(lastCall(f)[0]).toBe("/api/lineage/trace?table=c.s.t");
    });

    it("getColumnLineage", async () => {
      const f = makeFetch({ edges: [] });
      vi.stubGlobal("fetch", f);
      await api.getColumnLineage("c", "s", "t", "col");
      expect(lastCall(f)[0]).toBe("/api/column-lineage?catalog=c&schema=s&table=t&column=col");
    });

    it("getSchemaColumnLineage", async () => {
      const f = makeFetch({ edges: [] });
      vi.stubGlobal("fetch", f);
      await api.getSchemaColumnLineage("c", "s");
      expect(lastCall(f)[0]).toBe("/api/schema-column-lineage?catalog=c&schema=s");
    });

    it("getSharingOverlay with schema", async () => {
      const f = makeFetch({ audience: "both", shared_out: [], foreign_catalogs: [], available: true });
      vi.stubGlobal("fetch", f);
      await api.getSharingOverlay("c", "s", "both");
      expect(lastCall(f)[0]).toBe("/api/sharing/overlay?catalog=c&schema=s&audience=both");
    });

    it("getSharingOverlay without schema", async () => {
      const f = makeFetch({ audience: "provider", shared_out: [], foreign_catalogs: [], available: true });
      vi.stubGlobal("fetch", f);
      await api.getSharingOverlay("c", undefined, "provider");
      expect(lastCall(f)[0]).toBe("/api/sharing/overlay?catalog=c&audience=provider");
    });

    it("getSharingOverview", async () => {
      const f = makeFetch({ shares: [] });
      vi.stubGlobal("fetch", f);
      await api.getSharingOverview();
      expect(lastCall(f)[0]).toBe("/api/sharing/overview");
    });

    it("getEntityName", async () => {
      const f = makeFetch({ name: "job" });
      vi.stubGlobal("fetch", f);
      await api.getEntityName("JOB", "123");
      expect(lastCall(f)[0]).toBe("/api/entity-name?entity_type=JOB&entity_id=123");
    });

    it("getEntityRuns with defaults and with overrides", async () => {
      const f = makeFetch({ runs: [] });
      vi.stubGlobal("fetch", f);
      await api.getEntityRuns("JOB", "1");
      expect(lastCall(f)[0]).toBe("/api/observability/runs?entity_type=JOB&entity_id=1&limit=5");
      await api.getEntityRuns("JOB", "1", 10, true);
      expect(lastCall(f)[0]).toBe("/api/observability/runs?entity_type=JOB&entity_id=1&limit=10&refresh=true");
    });

    it("getHealth uses /health (no /api prefix)", async () => {
      const f = makeFetch({ status: "ok" });
      vi.stubGlobal("fetch", f);
      await api.getHealth();
      expect(lastCall(f)[0]).toBe("/health");
    });

    it("getAdminStatus", async () => {
      const f = makeFetch({ system: {} });
      vi.stubGlobal("fetch", f);
      await api.getAdminStatus();
      expect(lastCall(f)[0]).toBe("/api/admin/status");
    });

    it("getCapabilityCacheInventory", async () => {
      const f = makeFetch({ entries: [], count: 0 });
      vi.stubGlobal("fetch", f);
      await api.getCapabilityCacheInventory();
      expect(lastCall(f)[0]).toBe("/api/admin/capability-cache");
    });
  });

  // lineageExportUrl is a pure URL builder (no fetch).
  describe("lineageExportUrl", () => {
    it("builds catalog-only URL", () => {
      expect(api.lineageExportUrl("c a")).toBe("/api/lineage/export?catalog=c%20a");
    });
    it("builds catalog+schema URL", () => {
      expect(api.lineageExportUrl("c", "s")).toBe("/api/lineage/export?catalog=c&schema=s");
    });
  });

  // POST / DELETE helpers: assert method, headers, body + return, and error branch.
  describe("POST/DELETE methods", () => {
    it("upsertGovernanceRule sends POST with JSON body", async () => {
      const f = makeFetch({ rule_id: "r1", status: "ok" });
      vi.stubGlobal("fetch", f);
      const rule = { sensitivity: "high" };
      const out = await api.upsertGovernanceRule(rule);
      const [url, init] = lastCall(f);
      expect(url).toBe("/api/governance/config");
      expect(init.method).toBe("POST");
      expect(init.headers).toEqual({ "Content-Type": "application/json" });
      expect(init.body).toBe(JSON.stringify(rule));
      expect(out).toEqual({ rule_id: "r1", status: "ok" });
    });

    it("upsertGovernanceRule error branch", async () => {
      const f = makeFetch(null, { ok: false, status: 400, text: "bad" });
      vi.stubGlobal("fetch", f);
      await expect(api.upsertGovernanceRule({ sensitivity: "x" })).rejects.toThrow("API error 400: bad");
    });

    it("deleteGovernanceRule sends DELETE", async () => {
      const f = makeFetch({ rule_id: "r1", status: "deleted" });
      vi.stubGlobal("fetch", f);
      await api.deleteGovernanceRule("r 1");
      const [url, init] = lastCall(f);
      expect(url).toBe("/api/governance/config?rule_id=r%201");
      expect(init.method).toBe("DELETE");
    });

    it("deleteGovernanceRule error branch", async () => {
      const f = makeFetch(null, { ok: false, status: 404, text: "nope" });
      vi.stubGlobal("fetch", f);
      await expect(api.deleteGovernanceRule("r1")).rejects.toThrow("API error 404: nope");
    });

    it("analyzeProducer POST + return", async () => {
      const f = makeFetch({ source: "llm", columns: [] });
      vi.stubGlobal("fetch", f);
      const body = { entity_type: "JOB", entity_id: "1", target_table: "c.s.t" };
      const out = await api.analyzeProducer(body);
      const [url, init] = lastCall(f);
      expect(url).toBe("/api/analyze-producer");
      expect(init.method).toBe("POST");
      expect(init.body).toBe(JSON.stringify(body));
      expect(out.source).toBe("llm");
    });

    it("analyzeProducer error branch", async () => {
      const f = makeFetch(null, { ok: false, status: 500, text: "err" });
      vi.stubGlobal("fetch", f);
      await expect(
        api.analyzeProducer({ entity_type: "JOB", entity_id: "1", target_table: "t" }),
      ).rejects.toThrow("API error 500: err");
    });

    it("listColumnTransformationVersions POST + error", async () => {
      const f = makeFetch({ versions: [] });
      vi.stubGlobal("fetch", f);
      const body = { catalog: "c", schema_name: "s", table: "t" };
      await api.listColumnTransformationVersions(body);
      expect(lastCall(f)[0]).toBe("/api/column-transformations/versions");
      expect(lastCall(f)[1].body).toBe(JSON.stringify(body));

      const f2 = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f2);
      await expect(api.listColumnTransformationVersions(body)).rejects.toThrow("API error 500: e");
    });

    it("getTransformationVersion POST + error", async () => {
      const f = makeFetch({ ref: "llm:1" });
      vi.stubGlobal("fetch", f);
      const body = { catalog: "c", schema_name: "s", table: "t", ref: "llm:1" };
      await api.getTransformationVersion(body);
      expect(lastCall(f)[0]).toBe("/api/column-transformations/version");

      const f2 = makeFetch(null, { ok: false, status: 422, text: "e" });
      vi.stubGlobal("fetch", f2);
      await expect(api.getTransformationVersion(body)).rejects.toThrow("API error 422: e");
    });

    it("compareTransformationVersions POST + error", async () => {
      const f = makeFetch({ changed_count: 0 });
      vi.stubGlobal("fetch", f);
      const body = { catalog: "c", schema_name: "s", table: "t", ref_from: "a", ref_to: "b" };
      await api.compareTransformationVersions(body);
      expect(lastCall(f)[0]).toBe("/api/column-transformations/compare");

      const f2 = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f2);
      await expect(api.compareTransformationVersions(body)).rejects.toThrow("API error 500: e");
    });

    it("compareProducers POST + error", async () => {
      const f = makeFetch({ divergent_count: 0 });
      vi.stubGlobal("fetch", f);
      const body = { catalog: "c", schema_name: "s", table: "t", producers: [] };
      await api.compareProducers(body);
      expect(lastCall(f)[0]).toBe("/api/column-transformations/compare-producers");

      const f2 = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f2);
      await expect(api.compareProducers(body)).rejects.toThrow("API error 500: e");
    });

    it("resolveColumnTransformations POST + error", async () => {
      const f = makeFetch({ table_full_name: "c.s.t", columns: [] });
      vi.stubGlobal("fetch", f);
      const body = { catalog: "c", schema_name: "s", table: "t" };
      await api.resolveColumnTransformations(body);
      expect(lastCall(f)[0]).toBe("/api/column-transformations");

      const f2 = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f2);
      await expect(api.resolveColumnTransformations(body)).rejects.toThrow("API error 500: e");
    });

    it("invalidateTransform without tableFqn", async () => {
      const f = makeFetch({ status: "ok", scope: "cache" });
      vi.stubGlobal("fetch", f);
      await api.invalidateTransform("cache");
      const [url, init] = lastCall(f);
      expect(url).toBe("/api/transform/invalidate?scope=cache");
      expect(init.method).toBe("POST");
    });

    it("invalidateTransform with tableFqn", async () => {
      const f = makeFetch({ status: "ok", scope: "table" });
      vi.stubGlobal("fetch", f);
      await api.invalidateTransform("table", "c.s.t");
      expect(lastCall(f)[0]).toBe("/api/transform/invalidate?scope=table&table_fqn=c.s.t");
    });

    it("invalidateTransform error branch", async () => {
      const f = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f);
      await expect(api.invalidateTransform("all")).rejects.toThrow("API error 500: e");
    });

    it("evictCapabilityCache with all optional params", async () => {
      const f = makeFetch({ status: "ok", scope: "entry" });
      vi.stubGlobal("fetch", f);
      await api.evictCapabilityCache("entry", "c.s.t", "impact");
      const [url, init] = lastCall(f);
      expect(url).toBe("/api/admin/capability-cache/evict?scope=entry&table_fqn=c.s.t&tab=impact");
      expect(init.method).toBe("POST");
    });

    it("evictCapabilityCache scope only", async () => {
      const f = makeFetch({ status: "ok", scope: "all" });
      vi.stubGlobal("fetch", f);
      await api.evictCapabilityCache("all");
      expect(lastCall(f)[0]).toBe("/api/admin/capability-cache/evict?scope=all");
    });

    it("evictCapabilityCache error branch", async () => {
      const f = makeFetch(null, { ok: false, status: 500, text: "e" });
      vi.stubGlobal("fetch", f);
      await expect(api.evictCapabilityCache("table", "c.s.t")).rejects.toThrow("API error 500: e");
    });
  });

  describe("deepAnalyzeColumnTransformations (NDJSON streaming)", () => {
    function streamFetch(lines: string[], { ok = true }: { ok?: boolean } = {}) {
      return vi.fn(async () => ({
        ok,
        status: ok ? 200 : 500,
        text: async () => "boom",
        body: new ReadableStream({
          start(controller) {
            const enc = new TextEncoder();
            const blob = lines.join("");
            // Split mid-stream to exercise buffering across newline boundaries.
            const mid = Math.max(1, Math.floor(blob.length / 2));
            controller.enqueue(enc.encode(blob.slice(0, mid)));
            controller.enqueue(enc.encode(blob.slice(mid)));
            controller.close();
          },
        }),
      }));
    }

    it("parses each NDJSON line (incl. a newline-less tail) into onEvent", async () => {
      const lines = [
        JSON.stringify({ type: "step", step: "start", status: "running", message: "a" }) + "\n",
        JSON.stringify({ type: "step", step: "detect", status: "ok", message: "b" }) + "\n",
        JSON.stringify({ type: "result", derived: true, columns: [] }), // no trailing newline → tail flush
      ];
      vi.stubGlobal("fetch", streamFetch(lines));
      const events: any[] = [];
      await api.deepAnalyzeColumnTransformations(
        { catalog: "c", schema_name: "s", table: "t", entity_type: "PIPELINE", entity_id: "p1" },
        (ev) => events.push(ev),
      );
      expect(events).toHaveLength(3);
      expect(events[0].message).toBe("a");
      expect(events[2].type).toBe("result");
    });

    it("ignores malformed lines without throwing", async () => {
      vi.stubGlobal("fetch", streamFetch(["not-json\n", JSON.stringify({ type: "step", status: "ok", step: "x", message: "ok" }) + "\n"]));
      const events: any[] = [];
      await api.deepAnalyzeColumnTransformations(
        { catalog: "c", schema_name: "s", table: "t", entity_type: "PIPELINE", entity_id: "p1" },
        (ev) => events.push(ev),
      );
      expect(events).toHaveLength(1);
      expect(events[0].message).toBe("ok");
    });

    it("throws when the response is not ok", async () => {
      vi.stubGlobal("fetch", streamFetch([], { ok: false }));
      await expect(
        api.deepAnalyzeColumnTransformations(
          { catalog: "c", schema_name: "s", table: "t", entity_type: "PIPELINE", entity_id: "p1" },
          () => {},
        ),
      ).rejects.toThrow(/API error/);
    });
  });
});
