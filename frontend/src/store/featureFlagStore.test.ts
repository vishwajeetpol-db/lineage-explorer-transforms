import { describe, it, expect, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFeatureFlagStore, useFeatureFlagEnabled } from "./featureFlagStore";
import type { FeatureFlagCard } from "../api/controlPanel";

const get = () => useFeatureFlagStore.getState();

const flag = (id: string, enabled: boolean): FeatureFlagCard =>
  ({ id, enabled } as unknown as FeatureFlagCard);

describe("featureFlagStore", () => {
  beforeEach(() => {
    useFeatureFlagStore.setState({
      flags: [],
      loading: false,
      error: null,
      planCaptureStatus: null,
      federatedSyncStatus: null,
    });
  });

  it("has expected defaults", () => {
    expect(get().flags).toEqual([]);
    expect(get().loading).toBe(false);
    expect(get().error).toBeNull();
  });

  it("setFlags sets the list", () => {
    const flags = [flag("a", true)];
    get().setFlags(flags);
    expect(get().flags).toBe(flags);
  });

  it("setLoading toggles loading", () => {
    get().setLoading(true);
    expect(get().loading).toBe(true);
  });

  it("setError sets error and clears loading", () => {
    get().setLoading(true);
    get().setError("bad");
    expect(get().error).toBe("bad");
    expect(get().loading).toBe(false);
  });

  it("updateFlagEnabled flips only the matching flag", () => {
    get().setFlags([flag("a", false), flag("b", true)]);
    get().updateFlagEnabled("a", true);
    expect(get().flags.find((f) => f.id === "a")?.enabled).toBe(true);
    expect(get().flags.find((f) => f.id === "b")?.enabled).toBe(true);
  });

  it("updateFlagEnabled is a no-op for unknown id", () => {
    get().setFlags([flag("a", false)]);
    get().updateFlagEnabled("missing", true);
    expect(get().flags.find((f) => f.id === "a")?.enabled).toBe(false);
  });

  it("setPlanCaptureStatus / setFederatedSyncStatus", () => {
    get().setPlanCaptureStatus({ enabled: true } as any);
    get().setFederatedSyncStatus({ enabled: false } as any);
    expect(get().planCaptureStatus).toEqual({ enabled: true });
    expect(get().federatedSyncStatus).toEqual({ enabled: false });
    get().setPlanCaptureStatus(null);
    expect(get().planCaptureStatus).toBeNull();
  });

  describe("useFeatureFlagEnabled", () => {
    it("returns false when the flag is missing", () => {
      const { result } = renderHook(() => useFeatureFlagEnabled("nope"));
      expect(result.current).toBe(false);
    });

    it("returns the flag's enabled value when present", () => {
      get().setFlags([flag("cap", true)]);
      const { result } = renderHook(() => useFeatureFlagEnabled("cap"));
      expect(result.current).toBe(true);
    });
  });
});
