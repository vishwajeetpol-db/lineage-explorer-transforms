import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ModuleSection from "./ModuleSection";
import type { FeatureFlagCard } from "../../api/controlPanel";

function flag(id: string): FeatureFlagCard {
  return {
    id, module: "m", module_label: "Module", accent: "violet", name: `Flag ${id}`,
    description: "d", cost: "low", risk: "low", side_effects: [], access_requirements: [],
    depends_on: [], enabled: false, kill_switched: false,
  };
}

describe("ModuleSection", () => {
  const base = { isAdmin: true, busyFlagId: null, onToggle: vi.fn(), onOpenAccessCheck: vi.fn() };

  it("returns null when there are no flags", () => {
    const { container } = render(<ModuleSection moduleLabel="Empty" flags={[]} {...base} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders label, status slot, and cards", () => {
    render(
      <ModuleSection
        moduleLabel="Lineage Tracking"
        flags={[flag("a"), flag("b")]}
        statusSlot={<span>status here</span>}
        {...base}
      />,
    );
    expect(screen.getByText("Lineage Tracking")).toBeInTheDocument();
    expect(screen.getByText("status here")).toBeInTheDocument();
    expect(screen.getByText("Flag a")).toBeInTheDocument();
    expect(screen.getByText("Flag b")).toBeInTheDocument();
  });
});
