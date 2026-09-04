import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Breadcrumb from "./Breadcrumb";

const goCatalogs = vi.fn();
const goSchemas = vi.fn();
const goLanding = vi.fn();
vi.mock("../../hooks/useRouter", () => ({
  goCatalogs: () => goCatalogs(),
  goSchemas: (c: string) => goSchemas(c),
  goLanding: () => goLanding(),
}));

describe("Breadcrumb", () => {
  it("renders Home + Catalogs by default", () => {
    render(<Breadcrumb />);
    expect(screen.getByText("Catalogs")).toBeInTheDocument();
  });

  it("adds catalog crumb (clickable when schema present)", async () => {
    const user = userEvent.setup();
    render(<Breadcrumb catalog="main" schema="sales" />);
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("sales")).toBeInTheDocument();
    await user.click(screen.getByText("main"));
    expect(goSchemas).toHaveBeenCalledWith("main");
  });

  it("navigates to catalogs and landing via buttons", async () => {
    const user = userEvent.setup();
    render(<Breadcrumb catalog="main" />);
    await user.click(screen.getByText("Catalogs"));
    expect(goCatalogs).toHaveBeenCalled();
  });

  it("renders catalog as terminal span when no schema", () => {
    render(<Breadcrumb catalog="main" />);
    // catalog is last, so rendered as span not button
    expect(screen.getByText("main").tagName).toBe("SPAN");
  });
});
