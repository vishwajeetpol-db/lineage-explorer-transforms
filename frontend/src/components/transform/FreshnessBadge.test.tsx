import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FreshnessBadge } from "./FreshnessBadge";
import type { FreshnessInfo } from "../../api/transform";

function fresh(overrides: Partial<FreshnessInfo> = {}): FreshnessInfo {
  return { exists: true, edge_count: 5, last_built: "2024-01-01", age_str: "2h ago", is_stale: false, ...overrides };
}

describe("FreshnessBadge", () => {
  it("shows age when fresh", () => {
    render(<FreshnessBadge freshness={fresh()} />);
    expect(screen.getByText("2h ago")).toBeInTheDocument();
  });

  it("shows stale age", () => {
    render(<FreshnessBadge freshness={fresh({ is_stale: true, age_str: "3d ago" })} />);
    expect(screen.getByText("3d ago")).toBeInTheDocument();
  });

  it("shows Not built when lineage does not exist", () => {
    render(<FreshnessBadge freshness={fresh({ exists: false })} />);
    expect(screen.getByText("Not built")).toBeInTheDocument();
  });
});
