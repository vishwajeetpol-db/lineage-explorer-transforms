import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useRouter,
  navigate,
  goLanding,
  goCatalogs,
  goSchemas,
  goTables,
  goLineage,
  goTableLineage,
  goSchemaLineage,
  goCatalogLineage,
  goControlPanel,
  goAdmin,
  goDQ,
  goGlossary,
  goNotifications,
  goExport,
  goRootCause,
  goBiConsumers,
  goStreaming,
  type Route,
} from "./useRouter";

function setSearch(search: string) {
  window.history.replaceState({}, "", "/" + search);
}

describe("useRouter — parseRoute", () => {
  beforeEach(() => setSearch(""));
  afterEach(() => setSearch(""));

  const cases: [string, Route][] = [
    ["", { view: "landing" }],
    ["?admin=true", { view: "admin" }],
    ["?controlPanel=true", { view: "controlPanel" }],
    ["?view=tableLineage", { view: "tableLineage", table: undefined }],
    ["?view=tableLineage&table=a.b.c", { view: "tableLineage", table: "a.b.c" }],
    ["?view=tableLineage&table=bad", { view: "tableLineage", table: undefined }],
    ["?table=a.b.c", { view: "lineage", table: "a.b.c" }],
    ["?view=catalogs", { view: "catalogs" }],
    ["?view=schemas&catalog=c", { view: "schemas", catalog: "c" }],
    ["?view=tables&catalog=c&schema=s", { view: "tables", catalog: "c", schema: "s" }],
    ["?view=schemaLineage&catalog=c&schema=s", { view: "schemaLineage", catalog: "c", schema: "s" }],
    ["?view=catalogLineage&catalog=c", { view: "catalogLineage", catalog: "c" }],
    // A valid 3-part table is caught as `lineage` before the dq branch.
    ["?view=dq&table=a.b.c", { view: "lineage", table: "a.b.c" }],
    // An invalid table falls through to the dq branch with the raw value.
    ["?view=dq&table=notvalid", { view: "dq", table: "notvalid" }],
    ["?view=dq", { view: "dq", table: undefined }],
    ["?view=glossary", { view: "glossary" }],
    ["?view=notifications", { view: "notifications" }],
    ["?view=export", { view: "export" }],
    ["?view=rootCause", { view: "rootCause" }],
    ["?view=biConsumers", { view: "biConsumers" }],
    ["?view=streaming", { view: "streaming" }],
  ];

  it.each(cases)("parses %s", (search, expected) => {
    setSearch(search);
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual(expected);
  });

  it("falls back to landing for a bare table with wrong part count", () => {
    setSearch("?table=only.two");
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual({ view: "landing" });
  });

  it("falls back to landing when schemas is missing its catalog", () => {
    setSearch("?view=schemas");
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual({ view: "landing" });
  });

  it("falls back to landing when tables is missing schema", () => {
    setSearch("?view=tables&catalog=c");
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual({ view: "landing" });
  });

  it("falls back to landing when schemaLineage missing schema", () => {
    setSearch("?view=schemaLineage&catalog=c");
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual({ view: "landing" });
  });

  it("falls back to landing when catalogLineage missing catalog", () => {
    setSearch("?view=catalogLineage");
    const { result } = renderHook(() => useRouter());
    expect(result.current).toEqual({ view: "landing" });
  });
});

describe("useRouter — navigation + subscription", () => {
  beforeEach(() => setSearch(""));
  afterEach(() => setSearch(""));

  it("navigate(pushState) updates the route via custom event", () => {
    const { result } = renderHook(() => useRouter());
    act(() => navigate({ view: "catalogs" }));
    expect(result.current).toEqual({ view: "catalogs" });
    expect(window.location.search).toBe("?view=catalogs");
  });

  it("navigate(replace) uses replaceState", () => {
    const { result } = renderHook(() => useRouter());
    act(() => navigate({ view: "admin" }, true));
    expect(result.current).toEqual({ view: "admin" });
  });

  it("responds to popstate events", () => {
    const { result } = renderHook(() => useRouter());
    act(() => {
      window.history.pushState({}, "", "/?view=glossary");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current).toEqual({ view: "glossary" });
  });
});

describe("useRouter — routeToSearch via go* helpers", () => {
  beforeEach(() => setSearch(""));
  afterEach(() => setSearch(""));

  const expectSearch = (fn: () => void, search: string) => {
    act(fn);
    expect(window.location.search).toBe(search);
  };

  it("covers every go* helper's serialized query", () => {
    const { result } = renderHook(() => useRouter());
    expect(result).toBeTruthy();

    expectSearch(goCatalogs, "?view=catalogs");
    expectSearch(() => goSchemas("c a"), "?view=schemas&catalog=c%20a");
    expectSearch(() => goTables("c", "s"), "?view=tables&catalog=c&schema=s");
    expectSearch(() => goLineage("a.b.c"), "?table=a.b.c");
    expectSearch(() => goTableLineage("a.b.c"), "?view=tableLineage&table=a.b.c");
    expectSearch(() => goTableLineage(), "?view=tableLineage");
    expectSearch(() => goSchemaLineage("c", "s"), "?view=schemaLineage&catalog=c&schema=s");
    expectSearch(() => goCatalogLineage("c"), "?view=catalogLineage&catalog=c");
    expectSearch(goControlPanel, "?controlPanel=true");
    expectSearch(goAdmin, "?admin=true");
    expectSearch(() => goDQ("a.b.c"), "?view=dq&table=a.b.c");
    expectSearch(() => goDQ(), "?view=dq");
    expectSearch(goGlossary, "?view=glossary");
    expectSearch(goNotifications, "?view=notifications");
    expectSearch(goExport, "?view=export");
    expectSearch(goRootCause, "?view=rootCause");
    expectSearch(goBiConsumers, "?view=biConsumers");
    expectSearch(goStreaming, "?view=streaming");
    // landing serializes to empty search
    expectSearch(goLanding, "");
  });
});
