import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  parseFqn,
  SectionTitle,
  NoTable,
  Stat,
  SensitivityBadge,
  CacheHeader,
  PanelState,
} from "./panelShared";
import type { CacheMeta } from "../../api/client";

describe("parseFqn", () => {
  it("parses a valid 3-part FQN", () => {
    expect(parseFqn("cat.schema.tbl")).toEqual({ catalog: "cat", schema: "schema", table: "tbl" });
  });
  it("returns null for null input", () => {
    expect(parseFqn(null)).toBeNull();
  });
  it("returns null for a 2-part name", () => {
    expect(parseFqn("cat.schema")).toBeNull();
  });
  it("returns null for a 4-part name", () => {
    expect(parseFqn("a.b.c.d")).toBeNull();
  });
});

describe("SectionTitle", () => {
  it("renders its children", () => {
    render(<SectionTitle>My Section</SectionTitle>);
    expect(screen.getByText("My Section")).toBeInTheDocument();
  });
});

describe("NoTable", () => {
  it("prompts to select a table", () => {
    render(<NoTable />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });
});

describe("Stat", () => {
  it("renders label and value with default accent", () => {
    render(<Stat label="Downstream" value={42} />);
    expect(screen.getByText("Downstream")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });
  it("applies a custom accent class", () => {
    render(<Stat label="X" value="v" accent="text-rose-300" />);
    expect(screen.getByText("v").className).toContain("text-rose-300");
  });
});

describe("SensitivityBadge", () => {
  it("uses the known style for a mapped sensitivity", () => {
    render(<SensitivityBadge sensitivity="pii" />);
    const el = screen.getByText("pii");
    expect(el.className).toContain("rose");
  });
  it("falls back to violet for an unknown sensitivity", () => {
    render(<SensitivityBadge sensitivity="WEIRD" />);
    const el = screen.getByText("WEIRD");
    expect(el.className).toContain("violet");
  });
});

describe("CacheHeader", () => {
  const fresh: CacheMeta = { from_cache: true, cached_at: new Date().toISOString(), cached_by: "me", stale: false };
  const stale: CacheMeta = { from_cache: true, cached_at: new Date(Date.now() - 7200_000).toISOString(), cached_by: "me", stale: true };

  it("shows a fresh cached badge", () => {
    render(<CacheHeader cache={fresh} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/cached just now/)).toBeInTheDocument();
  });
  it("shows the stale warning", () => {
    render(<CacheHeader cache={stale} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/may be stale/)).toBeInTheDocument();
  });
  it("shows 'just refreshed' when not from cache", () => {
    render(<CacheHeader cache={{ ...fresh, from_cache: false }} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/just refreshed/)).toBeInTheDocument();
  });
  it("shows 'live' when no cache meta", () => {
    render(<CacheHeader cache={null} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText("live")).toBeInTheDocument();
  });
  it("calls onRefresh when the button is clicked", async () => {
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(<CacheHeader cache={fresh} loading={false} onRefresh={onRefresh} />);
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
  it("disables the refresh button while loading", () => {
    render(<CacheHeader cache={fresh} loading={true} onRefresh={() => {}} />);
    expect(screen.getByRole("button", { name: /Refresh/ })).toBeDisabled();
  });
  it("omits the tooltip title when cached_at is absent", () => {
    render(<CacheHeader cache={{ from_cache: true, cached_at: null, cached_by: null, stale: false }} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/cached/)).toBeInTheDocument();
  });
  it("renders minutes-ago and days-ago relative times", () => {
    const mins: CacheMeta = { from_cache: true, cached_at: new Date(Date.now() - 5 * 60_000).toISOString(), cached_by: null, stale: false };
    const { unmount } = render(<CacheHeader cache={mins} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/5m ago/)).toBeInTheDocument();
    unmount();
    const days: CacheMeta = { from_cache: true, cached_at: new Date(Date.now() - 3 * 86400_000).toISOString(), cached_by: null, stale: false };
    render(<CacheHeader cache={days} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/3d ago/)).toBeInTheDocument();
  });
  it("handles an invalid cached_at timestamp", () => {
    render(<CacheHeader cache={{ from_cache: true, cached_at: "not-a-date", cached_by: null, stale: false }} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/cached/)).toBeInTheDocument();
  });
});

describe("PanelState", () => {
  it("renders the loading state", () => {
    render(<PanelState loading error={null} empty={false}><div>child</div></PanelState>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
  it("renders the error state", () => {
    render(<PanelState loading={false} error="boom" empty={false}><div>child</div></PanelState>);
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
  it("renders the empty state with a custom label", () => {
    render(<PanelState loading={false} error={null} empty emptyLabel="nothing here"><div>child</div></PanelState>);
    expect(screen.getByText("nothing here")).toBeInTheDocument();
  });
  it("renders the default empty label", () => {
    render(<PanelState loading={false} error={null} empty><div>child</div></PanelState>);
    expect(screen.getByText("No data for this table.")).toBeInTheDocument();
  });
  it("renders children when not loading/error/empty", () => {
    render(<PanelState loading={false} error={null} empty={false}><div>child</div></PanelState>);
    expect(screen.getByText("child")).toBeInTheDocument();
  });
});
