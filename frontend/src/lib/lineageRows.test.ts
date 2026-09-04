import { describe, it, expect } from "vitest";
import { collapseToTableEdges, splitFqdn } from "./lineageRows";
import type { LineageEdge } from "../api/client";

describe("collapseToTableEdges", () => {
  it("passes through pure table→table edges", () => {
    const edges: LineageEdge[] = [
      { source: "a.b.c", target: "a.b.d" },
      { source: "a.b.d", target: "a.b.e" },
    ];
    expect(collapseToTableEdges(edges)).toEqual([
      { source: "a.b.c", target: "a.b.d" },
      { source: "a.b.d", target: "a.b.e" },
    ]);
  });

  it("collapses table → entity → table into direct edges", () => {
    const edges: LineageEdge[] = [
      { source: "src.t", target: "entity:pipe1" },
      { source: "entity:pipe1", target: "dst.t" },
    ];
    expect(collapseToTableEdges(edges)).toEqual([{ source: "src.t", target: "dst.t" }]);
  });

  it("fans out multiple sources and targets through one entity", () => {
    const edges: LineageEdge[] = [
      { source: "s1", target: "entity:p" },
      { source: "s2", target: "entity:p" },
      { source: "entity:p", target: "t1" },
      { source: "entity:p", target: "t2" },
    ];
    expect(collapseToTableEdges(edges)).toEqual([
      { source: "s1", target: "t1" },
      { source: "s1", target: "t2" },
      { source: "s2", target: "t1" },
      { source: "s2", target: "t2" },
    ]);
  });

  it("dedupes duplicate resulting edges", () => {
    const edges: LineageEdge[] = [
      { source: "a", target: "b" },
      { source: "a", target: "b" },
    ];
    expect(collapseToTableEdges(edges)).toEqual([{ source: "a", target: "b" }]);
  });

  it("drops self-loops", () => {
    const edges: LineageEdge[] = [{ source: "a", target: "a" }];
    expect(collapseToTableEdges(edges)).toEqual([]);
  });

  it("ignores an entity with only incoming edges (no targets)", () => {
    const edges: LineageEdge[] = [{ source: "s", target: "entity:p" }];
    expect(collapseToTableEdges(edges)).toEqual([]);
  });

  it("ignores an entity with only outgoing edges (no sources)", () => {
    const edges: LineageEdge[] = [{ source: "entity:p", target: "t" }];
    expect(collapseToTableEdges(edges)).toEqual([]);
  });
});

describe("splitFqdn", () => {
  it("splits a 3-part name", () => {
    expect(splitFqdn("cat.sch.tbl")).toEqual({ catalog: "cat", schema: "sch" });
  });

  it("returns empty parts for a non-3-part name", () => {
    expect(splitFqdn("cat.sch")).toEqual({ catalog: "", schema: "" });
    expect(splitFqdn("cat.sch.tbl.extra")).toEqual({ catalog: "", schema: "" });
  });
});
