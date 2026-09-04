import { describe, it, expect, afterEach, vi } from "vitest";
import {
  getTransformFreshness,
  getTransformTrace,
  submitTransformBuild,
  getBuildStatus,
  getTransformCategories,
  getBuildConfig,
  getCapturedExpression,
} from "./transform";

function makeFetch(
  payload: unknown,
  opts: { ok?: boolean; status?: number; jsonThrows?: boolean; statusText?: string } = {},
) {
  const { ok = true, status = 200, jsonThrows = false, statusText = "" } = opts;
  return vi.fn(async () => ({
    ok,
    status,
    statusText,
    json: async () => {
      if (jsonThrows) throw new Error("not json");
      return payload;
    },
  }));
}

function lastCall(mock: ReturnType<typeof vi.fn>) {
  return mock.mock.calls[mock.mock.calls.length - 1];
}

describe("api/transform", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("getTransformFreshness builds query params", async () => {
    const f = makeFetch({ exists: true, edge_count: 3 });
    vi.stubGlobal("fetch", f);
    const out = await getTransformFreshness("c", "s", "t");
    expect(lastCall(f)[0]).toBe("/api/transform/freshness?catalog=c&schema=s&table=t");
    expect(out.exists).toBe(true);
  });

  it("getTransformTrace with default maxDepth", async () => {
    const f = makeFetch({ levels: [], has_lineage: true });
    vi.stubGlobal("fetch", f);
    await getTransformTrace("c", "s", "t", "col");
    expect(lastCall(f)[0]).toBe(
      "/api/transform/trace?catalog=c&schema=s&table=t&column=col&max_depth=8",
    );
  });

  it("getTransformTrace with explicit maxDepth", async () => {
    const f = makeFetch({ levels: [], has_lineage: true });
    vi.stubGlobal("fetch", f);
    await getTransformTrace("c", "s", "t", "col", 3);
    expect(lastCall(f)[0]).toBe(
      "/api/transform/trace?catalog=c&schema=s&table=t&column=col&max_depth=3",
    );
  });

  it("submitTransformBuild with default forceRebuild", async () => {
    const f = makeFetch({ status: "submitted" });
    vi.stubGlobal("fetch", f);
    await submitTransformBuild("c.s.t");
    const [url, init] = lastCall(f);
    expect(url).toBe("/api/transform/build");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual({ table_fqn: "c.s.t", force_rebuild: false });
  });

  it("submitTransformBuild with forceRebuild true", async () => {
    const f = makeFetch({ status: "submitted" });
    vi.stubGlobal("fetch", f);
    await submitTransformBuild("c.s.t", true);
    expect(JSON.parse(lastCall(f)[1].body).force_rebuild).toBe(true);
  });

  it("getBuildStatus uses run id in path", async () => {
    const f = makeFetch({ run_id: "42", state: "RUNNING" });
    vi.stubGlobal("fetch", f);
    await getBuildStatus("42");
    expect(lastCall(f)[0]).toBe("/api/transform/status/42");
  });

  it("getTransformCategories", async () => {
    const f = makeFetch({ categories: {}, level_colors: [] });
    vi.stubGlobal("fetch", f);
    await getTransformCategories();
    expect(lastCall(f)[0]).toBe("/api/transform/categories");
  });

  it("getBuildConfig", async () => {
    const f = makeFetch({ configured: true });
    vi.stubGlobal("fetch", f);
    const out = await getBuildConfig();
    expect(lastCall(f)[0]).toBe("/api/transform/build-configured");
    expect(out.configured).toBe(true);
  });

  it("getCapturedExpression unwraps the captured field", async () => {
    const captured = {
      target_column: "x",
      source_columns: ["a"],
      expression: "a+1",
      confidence: 0.9,
      notes: "",
      captured_via: null,
      captured_at: null,
      version: 1,
    };
    const f = makeFetch({ captured });
    vi.stubGlobal("fetch", f);
    const out = await getCapturedExpression("c", "s", "t", "x");
    expect(lastCall(f)[0]).toBe(
      "/api/transform/captured-expression?catalog=c&schema=s&table=t&column=x",
    );
    expect(out).toEqual(captured);
  });

  it("getCapturedExpression returns null when nothing captured", async () => {
    const f = makeFetch({ captured: null });
    vi.stubGlobal("fetch", f);
    const out = await getCapturedExpression("c", "s", "t", "x");
    expect(out).toBeNull();
  });

  it("error branch surfaces body.detail", async () => {
    const f = makeFetch({ detail: "boom" }, { ok: false, status: 500 });
    vi.stubGlobal("fetch", f);
    await expect(getTransformFreshness("c", "s", "t")).rejects.toThrow("boom");
  });

  it("error branch falls back to statusText when json throws", async () => {
    const f = makeFetch(null, { ok: false, status: 503, jsonThrows: true, statusText: "Unavailable" });
    vi.stubGlobal("fetch", f);
    await expect(getBuildConfig()).rejects.toThrow("Unavailable");
  });

  it("error branch falls back to HTTP <status> when detail/statusText empty", async () => {
    const f = makeFetch({}, { ok: false, status: 404, statusText: "" });
    vi.stubGlobal("fetch", f);
    await expect(getBuildConfig()).rejects.toThrow("HTTP 404");
  });
});
