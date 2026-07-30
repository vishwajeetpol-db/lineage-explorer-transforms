import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ControlPanel from "./ControlPanel";
import { useLineageStore } from "../../store/lineageStore";
import { useFeatureFlagStore } from "../../store/featureFlagStore";
import * as cpApi from "../../api/controlPanel";
import type { FeatureFlagCard } from "../../api/controlPanel";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
vi.mock("../../api/controlPanel", () => ({
  getFeatureFlags: vi.fn(),
  setFeatureFlag: vi.fn(),
  getPlanCaptureStatus: vi.fn(),
  getFederatedSyncStatus: vi.fn(),
  checkAccessRequirements: vi.fn(),
}));

function flag(id: string, module_label: string): FeatureFlagCard {
  return {
    id, module: "m", module_label, accent: "violet", name: `Flag ${id}`,
    description: "d", cost: "low", risk: "low", side_effects: [], access_requirements: [],
    depends_on: [], enabled: false, kill_switched: false,
  };
}

describe("ControlPanel", () => {
  beforeEach(() => {
    useLineageStore.setState({ isAdmin: true });
    useFeatureFlagStore.setState({ flags: [], loading: false, error: null, planCaptureStatus: null, federatedSyncStatus: null });
    (cpApi.getFeatureFlags as any).mockResolvedValue({
      flags: [flag("a", "Lineage Tracking"), flag("b", "Column Transformation"), flag("c", "Federated Sync")],
    });
    (cpApi.getPlanCaptureStatus as any).mockResolvedValue({ enabled: true, table_reachable: true, captured_plan_count: 12, captured_cdc_spec_count: 2, distinct_targets: 4 });
    (cpApi.getFederatedSyncStatus as any).mockResolvedValue({ enabled: true, registered_peers: 3, known_shares: 5, reachable_overlap: 2 });
    (cpApi.setFeatureFlag as any).mockResolvedValue({ status: "ok", flag_id: "a", enabled: true });
  });

  it("renders nothing when closed", () => {
    render(<ControlPanel open={false} onClose={vi.fn()} />);
    expect(screen.queryByText("Control Panel")).not.toBeInTheDocument();
  });

  it("loads flags and status cards when opened", async () => {
    render(<ControlPanel open onClose={vi.fn()} />);
    expect(await screen.findByText("Flag a")).toBeInTheDocument();
    expect(screen.getByText(/12 plans/)).toBeInTheDocument();
    expect(screen.getByText(/3 peers/)).toBeInTheDocument();
  });

  it("closes via the close button", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ControlPanel open onClose={onClose} />);
    await screen.findByText("Flag a");
    await user.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("toggles a flag optimistically and confirms via api", async () => {
    const user = userEvent.setup();
    render(<ControlPanel open onClose={vi.fn()} />);
    await screen.findByText("Flag a");
    const switches = screen.getAllByRole("switch");
    await user.click(switches[0]);
    expect(cpApi.setFeatureFlag).toHaveBeenCalled();
  });

  it("reverts and shows error when toggle fails", async () => {
    (cpApi.setFeatureFlag as any).mockRejectedValue(new Error("toggle failed"));
    const user = userEvent.setup();
    render(<ControlPanel open onClose={vi.fn()} />);
    await screen.findByText("Flag a");
    await user.click(screen.getAllByRole("switch")[0]);
    expect(await screen.findByText("toggle failed")).toBeInTheDocument();
  });

  it("shows load error", async () => {
    (cpApi.getFeatureFlags as any).mockRejectedValue(new Error("load failed"));
    render(<ControlPanel open onClose={vi.fn()} />);
    expect(await screen.findByText("load failed")).toBeInTheDocument();
  });

  it("shows read-only badge for non-admins", async () => {
    useLineageStore.setState({ isAdmin: false });
    const { container } = render(<ControlPanel open onClose={vi.fn()} />);
    await screen.findByText("Flag a");
    expect(container.textContent).toContain("Read-only");
  });

  it("shows empty state when no capabilities", async () => {
    (cpApi.getFeatureFlags as any).mockResolvedValue({ flags: [] });
    const { container } = render(<ControlPanel open onClose={vi.fn()} />);
    await screen.findByText("Control Panel");
    // status calls resolve async; wait a tick for flags to settle empty
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toContain("No capabilities registered.");
  });
});
