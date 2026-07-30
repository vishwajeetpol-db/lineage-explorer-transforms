import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ThemeToggle from "./ThemeToggle";
import { useThemeStore } from "../../store/themeStore";

describe("ThemeToggle", () => {
  beforeEach(() => {
    useThemeStore.getState().setTheme("dark");
  });

  it("renders a labeled toggle button", () => {
    render(<ThemeToggle />);
    expect(screen.getByLabelText("Toggle theme")).toBeInTheDocument();
  });

  it("toggles the store theme on click", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    expect(useThemeStore.getState().theme).toBe("dark");
    await user.click(screen.getByLabelText("Toggle theme"));
    expect(useThemeStore.getState().theme).toBe("light");
  });

  it("applies a passed className", () => {
    render(<ThemeToggle className="custom-cls" />);
    expect(screen.getByLabelText("Toggle theme").className).toContain("custom-cls");
  });
});
