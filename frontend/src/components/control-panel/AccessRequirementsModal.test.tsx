import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccessRequirementsModal from "./AccessRequirementsModal";
import type { FeatureFlagCard } from "../../api/controlPanel";
import { checkAccessRequirements } from "../../api/controlPanel";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
vi.mock("../../api/controlPanel", () => ({
  checkAccessRequirements: vi.fn(),
}));

function flag(overrides: Partial<FeatureFlagCard> = {}): FeatureFlagCard {
  return {
    id: "f1", module: "m", module_label: "Module", accent: "violet", name: "Flag One",
    description: "d", cost: "low", risk: "low", side_effects: [],
    access_requirements: [
      { privilege: "SELECT", scope: "cat.sch.tbl", reason: "read data", satisfied: null, detail: "some detail" },
      { privilege: "MODIFY", scope: "cat.sch", reason: "write", satisfied: false },
    ],
    depends_on: [], enabled: false, kill_switched: false, ...overrides,
  };
}

describe("AccessRequirementsModal", () => {
  beforeEach(() => {
    (checkAccessRequirements as any).mockReset();
  });

  it("renders nothing when flag is null", () => {
    const { container } = render(<AccessRequirementsModal flag={null} onClose={vi.fn()} />);
    expect(container.querySelector("div")).toBeNull();
  });

  it("renders the declared requirements", () => {
    render(<AccessRequirementsModal flag={flag()} onClose={vi.fn()} />);
    expect(screen.getByText("Flag One")).toBeInTheDocument();
    expect(screen.getByText("SELECT")).toBeInTheDocument();
    expect(screen.getByText("MODIFY")).toBeInTheDocument();
    expect(screen.getByText("some detail")).toBeInTheDocument();
  });

  it("closes via the close button", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AccessRequirementsModal flag={flag()} onClose={onClose} />);
    await user.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("runs a live access check and shows results", async () => {
    (checkAccessRequirements as any).mockResolvedValue({
      flag_id: "f1",
      requirements: [{ privilege: "USE CATALOG", scope: "cat", reason: "checked", satisfied: true }],
    });
    const user = userEvent.setup();
    render(<AccessRequirementsModal flag={flag()} onClose={vi.fn()} />);
    await user.click(screen.getByText("Run live access check"));
    expect(await screen.findByText("USE CATALOG")).toBeInTheDocument();
  });

  it("shows an error when the check fails", async () => {
    (checkAccessRequirements as any).mockRejectedValue(new Error("boom check"));
    const user = userEvent.setup();
    render(<AccessRequirementsModal flag={flag()} onClose={vi.fn()} />);
    await user.click(screen.getByText("Run live access check"));
    expect(await screen.findByText("boom check")).toBeInTheDocument();
  });
});
