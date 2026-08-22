import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationsPanel } from "./NotificationsPanel";
import { useLineageStore } from "../store/lineageStore";

const notif = {
  notif_id: "n1", notif_type: "schema_change", severity: "critical", title: "Schema changed",
  detail: "column added", table_fqn: "main.s.t", column_name: "c", detected_at: "2024-05-01T10:00:00Z", is_read: false,
};

function routeFetch(handlers: Record<string, any>) {
  return vi.fn().mockImplementation((url: string) => {
    for (const key of Object.keys(handlers)) {
      if (url.includes(key)) return Promise.resolve({ ok: true, json: async () => handlers[key] });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

describe("NotificationsPanel", () => {
  beforeEach(() => {
    vi.spyOn(window, "alert").mockImplementation(() => {});
    // "Run Scan" hits an admin-gated endpoint, so the control is admin-only.
    useLineageStore.setState({ isAdmin: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useLineageStore.setState({ isAdmin: false });
  });

  it("loads notifications and unread count on mount", async () => {
    global.fetch = routeFetch({
      "unread-count": { count: 2 },
      "/api/notifications?": { notifications: [notif] },
    }) as any;
    render(<NotificationsPanel />);
    expect(await screen.findByText("Schema changed")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows empty state", async () => {
    global.fetch = routeFetch({ "unread-count": { count: 0 }, "/api/notifications?": { notifications: [] } }) as any;
    render(<NotificationsPanel />);
    expect(await screen.findByText(/No notifications/)).toBeInTheDocument();
  });

  it("filters by type", async () => {
    const fetchMock = routeFetch({ "unread-count": { count: 0 }, "/api/notifications?": { notifications: [] } });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<NotificationsPanel />);
    await screen.findByText(/No notifications/);
    await user.click(screen.getByText("Schema Change"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("notif_type=schema_change"))).toBe(true);
    });
  });

  it("marks all read", async () => {
    const fetchMock = routeFetch({ "unread-count": { count: 1 }, "/api/notifications?": { notifications: [notif] } });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<NotificationsPanel />);
    await screen.findByText("Schema changed");
    await user.click(screen.getByText("Mark all read"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("mark-read"))).toBe(true);
    });
  });

  it("triggers a scan", async () => {
    const fetchMock = routeFetch({
      "unread-count": { count: 0 },
      "/api/notifications?": { notifications: [] },
      "scan": { detected: { schema_change: 1 } },
    });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<NotificationsPanel />);
    await screen.findByText(/No notifications/);
    await user.click(screen.getByText("Run Scan"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("scan"))).toBe(true);
    });
  });

  it("hides Run Scan from non-admins instead of offering a control that 403s", async () => {
    useLineageStore.setState({ isAdmin: false });
    global.fetch = routeFetch({
      "unread-count": { count: 0 },
      "/api/notifications?": { notifications: [] },
    }) as any;
    render(<NotificationsPanel />);
    await screen.findByText(/No notifications/);
    expect(screen.queryByText("Run Scan")).not.toBeInTheDocument();
  });

  it("surfaces a failed scan instead of reporting a false all-clear", async () => {
    // fetch() does not reject on 4xx, so without an explicit res.ok check the
    // 403 body parsed fine, the catch never ran, and the user was shown
    // "Scan complete: undefined" over an unchanged list — reading as "the
    // detectors found nothing".
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("scan")) {
        return Promise.resolve({
          ok: false,
          status: 403,
          json: async () => ({ detail: "Admin required" }),
        });
      }
      if (String(url).includes("unread-count")) {
        return Promise.resolve({ ok: true, json: async () => ({ count: 0 }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ notifications: [] }) });
    });
    global.fetch = fetchMock as any;
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const user = userEvent.setup();
    render(<NotificationsPanel />);
    await screen.findByText(/No notifications/);
    await user.click(screen.getByText("Run Scan"));
    expect(await screen.findByText("Admin required")).toBeInTheDocument();
    expect(alertSpy).not.toHaveBeenCalled();
  });
});
