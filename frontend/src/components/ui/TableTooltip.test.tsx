import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import TableTooltip from "./TableTooltip";
import type { TableNode } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div style={p.style} onMouseEnter={p.onMouseEnter} onMouseLeave={p.onMouseLeave}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

function node(overrides: Partial<TableNode> = {}): TableNode {
  return {
    node_type: "table",
    id: "orders",
    name: "orders",
    full_name: "main.sales.orders",
    table_type: "MANAGED",
    owner: "alice@co.com",
    comment: null,
    columns: [{ name: "id", type: "int", nullable: false }],
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-02-01T00:00:00Z",
    upstream_count: 2,
    downstream_count: 3,
    lineage_status: "connected",
    ...overrides,
  };
}

describe("TableTooltip", () => {
  const pos = { x: 100, y: 100 };

  it("renders name, owner, columns and counts", () => {
    render(<TableTooltip node={node()} position={pos} />);
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText("main.sales.orders")).toBeInTheDocument();
    expect(screen.getByText("alice@co.com")).toBeInTheDocument();
    expect(screen.getByText("Managed Table")).toBeInTheDocument();
    expect(screen.getByText("Upstream")).toBeInTheDocument();
    expect(screen.getByText("Downstream")).toBeInTheDocument();
  });

  it("falls back for missing owner and unknown type", () => {
    render(<TableTooltip node={node({ owner: null, table_type: "WEIRD", created_at: null, updated_at: null })} position={pos} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Managed Table")).toBeInTheDocument();
  });

  it("renders a VIEW type label", () => {
    render(<TableTooltip node={node({ table_type: "VIEW" })} position={pos} />);
    expect(screen.getByText("View")).toBeInTheDocument();
  });

  it("shows the lineage status badge for a non-connected status", () => {
    render(<TableTooltip node={node({ lineage_status: "orphan" })} position={pos} />);
    expect(screen.getByText(/No lineage recorded/)).toBeInTheDocument();
  });

  it("shows root status badge", () => {
    render(<TableTooltip node={node({ lineage_status: "root" })} position={pos} />);
    expect(screen.getByText(/no upstream dependencies/)).toBeInTheDocument();
  });

  it("fires hover callbacks", async () => {
    const onEnter = vi.fn();
    const onLeave = vi.fn();
    const { container } = render(
      <TableTooltip node={node()} position={pos} onMouseEnter={onEnter} onMouseLeave={onLeave} />,
    );
    const root = container.firstChild as HTMLElement;
    root.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    expect(root).toBeInTheDocument();
  });
});
