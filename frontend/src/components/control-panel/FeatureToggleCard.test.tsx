import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FeatureToggleCard from "./FeatureToggleCard";
import type { FeatureFlagCard } from "../../api/controlPanel";

function flag(overrides: Partial<FeatureFlagCard> = {}): FeatureFlagCard {
  return {
    id: "f1",
    module: "m",
    module_label: "Module",
    accent: "violet",
    name: "My Flag",
    description: "does a thing",
    cost: "low",
    risk: "low",
    side_effects: ["writes a table"],
    access_requirements: [{ privilege: "SELECT", scope: "cat.sch", reason: "read", satisfied: true }],
    depends_on: ["other"],
    enabled: false,
    kill_switched: false,
    ...overrides,
  };
}

describe("FeatureToggleCard", () => {
  it("renders name and module label", () => {
    render(<FeatureToggleCard flag={flag()} isAdmin busy={false} onToggle={vi.fn()} onOpenAccessCheck={vi.fn()} />);
    expect(screen.getByText("My Flag")).toBeInTheDocument();
    expect(screen.getByText("Module")).toBeInTheDocument();
  });

  it("expands to show description, side effects, depends on, and access-check button", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(<FeatureToggleCard flag={flag()} isAdmin busy={false} onToggle={vi.fn()} onOpenAccessCheck={onOpen} />);
    await user.click(screen.getByText("My Flag"));
    expect(screen.getByText("does a thing")).toBeInTheDocument();
    expect(screen.getByText("writes a table")).toBeInTheDocument();
    expect(screen.getByText(/View access requirements/)).toBeInTheDocument();
    await user.click(screen.getByText(/View access requirements/));
    expect(onOpen).toHaveBeenCalled();
  });

  it("toggles the switch when admin", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(<FeatureToggleCard flag={flag()} isAdmin busy={false} onToggle={onToggle} onOpenAccessCheck={vi.fn()} />);
    await user.click(screen.getByRole("switch"));
    expect(onToggle).toHaveBeenCalledWith("f1", true);
  });

  it("disables the switch for non-admins", () => {
    render(<FeatureToggleCard flag={flag()} isAdmin={false} busy={false} onToggle={vi.fn()} onOpenAccessCheck={vi.fn()} />);
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("shows kill-switched badge and disables toggle", () => {
    render(<FeatureToggleCard flag={flag({ kill_switched: true })} isAdmin busy={false} onToggle={vi.fn()} onOpenAccessCheck={vi.fn()} />);
    expect(screen.getByText("Kill-switched")).toBeInTheDocument();
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("falls back to indigo accent for unknown accent", () => {
    render(<FeatureToggleCard flag={flag({ accent: "weird" })} isAdmin busy={false} onToggle={vi.fn()} onOpenAccessCheck={vi.fn()} />);
    expect(screen.getByText("My Flag")).toBeInTheDocument();
  });
});
