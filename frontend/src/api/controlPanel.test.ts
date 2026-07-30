import { describe, it, expect, afterEach, vi } from "vitest";
import {
  getFeatureFlags,
  setFeatureFlag,
  checkAccessRequirements,
  getPlanCaptureStatus,
  getFederatedSyncStatus,
  listFederatedPeers,
  registerFederatedPeer,
} from "./controlPanel";

// fetch mock. On error, controlPanel's fetchJson calls resp.json() first (for a
// { detail } payload) and only falls back to statusText if json() rejects.
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

describe("api/controlPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("getFeatureFlags", async () => {
    const f = makeFetch({ flags: [] });
    vi.stubGlobal("fetch", f);
    const out = await getFeatureFlags();
    expect(lastCall(f)[0]).toBe("/api/control-panel/flags");
    expect(out).toEqual({ flags: [] });
  });

  it("setFeatureFlag sends POST with encoded id and JSON body", async () => {
    const f = makeFetch({ status: "ok", flag_id: "a b", enabled: true });
    vi.stubGlobal("fetch", f);
    await setFeatureFlag("a b", true);
    const [url, init] = lastCall(f);
    expect(url).toBe("/api/control-panel/flags/a%20b");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.body).toBe(JSON.stringify({ enabled: true }));
  });

  it("checkAccessRequirements", async () => {
    const f = makeFetch({ flag_id: "f1", requirements: [] });
    vi.stubGlobal("fetch", f);
    await checkAccessRequirements("f1");
    expect(lastCall(f)[0]).toBe("/api/control-panel/access-check/f1");
  });

  it("getPlanCaptureStatus", async () => {
    const f = makeFetch({ enabled: true });
    vi.stubGlobal("fetch", f);
    await getPlanCaptureStatus();
    expect(lastCall(f)[0]).toBe("/api/control-panel/plan-capture/status");
  });

  it("getFederatedSyncStatus", async () => {
    const f = makeFetch({ enabled: false });
    vi.stubGlobal("fetch", f);
    await getFederatedSyncStatus();
    expect(lastCall(f)[0]).toBe("/api/control-panel/federated/status");
  });

  it("listFederatedPeers", async () => {
    const f = makeFetch({ peers: [] });
    vi.stubGlobal("fetch", f);
    await listFederatedPeers();
    expect(lastCall(f)[0]).toBe("/api/control-panel/federated/peers");
  });

  it("registerFederatedPeer sends POST with defaulted notes", async () => {
    const f = makeFetch({ status: "ok" });
    vi.stubGlobal("fetch", f);
    await registerFederatedPeer("alias", "share", "out");
    const [url, init] = lastCall(f);
    expect(url).toBe("/api/control-panel/federated/peers");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      peer_alias: "alias",
      share_name: "share",
      direction: "out",
      notes: "",
    });
  });

  it("registerFederatedPeer passes explicit notes", async () => {
    const f = makeFetch({ status: "ok" });
    vi.stubGlobal("fetch", f);
    await registerFederatedPeer("a", "s", "in", "hello");
    expect(JSON.parse(lastCall(f)[1].body).notes).toBe("hello");
  });

  it("error branch surfaces body.detail", async () => {
    const f = makeFetch({ detail: "forbidden" }, { ok: false, status: 403 });
    vi.stubGlobal("fetch", f);
    await expect(getFeatureFlags()).rejects.toThrow("forbidden");
  });

  it("error branch falls back to HTTP status when json() throws", async () => {
    const f = makeFetch(null, { ok: false, status: 502, jsonThrows: true, statusText: "Bad Gateway" });
    vi.stubGlobal("fetch", f);
    // json() rejects -> catch yields { detail: statusText } -> throws statusText.
    await expect(getFeatureFlags()).rejects.toThrow("Bad Gateway");
  });

  it("error branch falls back to HTTP <status> when detail and statusText empty", async () => {
    const f = makeFetch({}, { ok: false, status: 500, statusText: "" });
    vi.stubGlobal("fetch", f);
    await expect(getFeatureFlags()).rejects.toThrow("HTTP 500");
  });
});
