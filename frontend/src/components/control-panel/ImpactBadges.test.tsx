import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ImpactBadges from "./ImpactBadges";

describe("ImpactBadges", () => {
  it("renders cost and risk labels", () => {
    render(<ImpactBadges cost="high" risk="medium" />);
    expect(screen.getByText(/Cost:/)).toHaveTextContent("Cost: high");
    expect(screen.getByText(/Risk:/)).toHaveTextContent("Risk: medium");
  });

  it("falls back to low style for unknown level", () => {
    render(<ImpactBadges cost="unknown" risk="low" />);
    expect(screen.getByText(/Cost:/)).toHaveTextContent("Cost: unknown");
  });
});
