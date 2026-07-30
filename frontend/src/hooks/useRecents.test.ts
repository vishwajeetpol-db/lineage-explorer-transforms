import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRecents } from "./useRecents";

const KEY = "bricktrace:recents";

describe("useRecents", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("initializes empty when storage is blank", () => {
    const { result } = renderHook(() => useRecents());
    expect(result.current.recents).toEqual([]);
  });

  it("reads existing valid recents from storage", () => {
    localStorage.setItem(KEY, JSON.stringify(["a.b.c", "d.e.f"]));
    const { result } = renderHook(() => useRecents());
    expect(result.current.recents).toEqual(["a.b.c", "d.e.f"]);
  });

  it("filters out non-3-part and non-string entries on read", () => {
    localStorage.setItem(KEY, JSON.stringify(["a.b.c", "bad", 123, "x.y"]));
    const { result } = renderHook(() => useRecents());
    expect(result.current.recents).toEqual(["a.b.c"]);
  });

  it("returns [] when stored value is not an array", () => {
    localStorage.setItem(KEY, JSON.stringify({ not: "array" }));
    const { result } = renderHook(() => useRecents());
    expect(result.current.recents).toEqual([]);
  });

  it("returns [] when stored value is invalid JSON", () => {
    localStorage.setItem(KEY, "{not json");
    const { result } = renderHook(() => useRecents());
    expect(result.current.recents).toEqual([]);
  });

  it("addRecent prepends and persists", () => {
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("a.b.c"));
    expect(result.current.recents).toEqual(["a.b.c"]);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual(["a.b.c"]);
  });

  it("addRecent dedupes, moving existing entry to front", () => {
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("a.b.c"));
    act(() => result.current.addRecent("d.e.f"));
    act(() => result.current.addRecent("a.b.c"));
    expect(result.current.recents).toEqual(["a.b.c", "d.e.f"]);
  });

  it("addRecent ignores invalid fqdn", () => {
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("not.valid"));
    act(() => result.current.addRecent(""));
    expect(result.current.recents).toEqual([]);
  });

  it("addRecent caps at 10 entries", () => {
    const { result } = renderHook(() => useRecents());
    act(() => {
      for (let i = 0; i < 15; i++) result.current.addRecent(`c.s.t${i}`);
    });
    expect(result.current.recents).toHaveLength(10);
    // Most recent first
    expect(result.current.recents[0]).toBe("c.s.t14");
  });

  it("clearRecents empties the list and storage", () => {
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("a.b.c"));
    act(() => result.current.clearRecents());
    expect(result.current.recents).toEqual([]);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual([]);
  });

  it("syncs across tabs via the storage event", () => {
    const { result } = renderHook(() => useRecents());
    localStorage.setItem(KEY, JSON.stringify(["x.y.z"]));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: KEY }));
    });
    expect(result.current.recents).toEqual(["x.y.z"]);
  });

  it("ignores storage events for other keys", () => {
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("a.b.c"));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "other" }));
    });
    expect(result.current.recents).toEqual(["a.b.c"]);
  });

  it("addRecent survives storage write failure (quota)", () => {
    const spy = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    const { result } = renderHook(() => useRecents());
    act(() => result.current.addRecent("a.b.c"));
    // State still updates even though persistence threw
    expect(result.current.recents).toEqual(["a.b.c"]);
    spy.mockRestore();
  });
});
