import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Launcher } from "./Launcher";
import { useWindows } from "../store/windows";

function openLauncher() {
  // toggleLauncher flips launcherOpen (and clears the query)
  useWindows.getState().toggleLauncher();
}

describe("Launcher", () => {
  beforeEach(() => {
    useWindows.getState().reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing when the launcher is closed", () => {
    const { container } = render(<Launcher />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the available apps when open", () => {
    openLauncher();
    render(<Launcher />);
    expect(screen.getByRole("button", { name: "Browse" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Credentials" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
  });

  it("filters the grid by the search input", async () => {
    openLauncher();
    render(<Launcher />);

    await userEvent.type(screen.getByPlaceholderText("Search applications"), "cred");

    expect(screen.getByRole("button", { name: "Credentials" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Browse" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
    expect(useWindows.getState().launcherQuery).toBe("cred");
  });

  it("opens an app on click and closes the launcher", async () => {
    openLauncher();
    render(<Launcher />);
    expect(useWindows.getState().open).not.toContain("review");

    await userEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(useWindows.getState().open).toContain("review");
    // openApp closes the launcher
    expect(useWindows.getState().launcherOpen).toBe(false);
  });

  it("opens an app when Enter is pressed on a focused tile", async () => {
    openLauncher();
    render(<Launcher />);
    expect(useWindows.getState().open).not.toContain("pipeline");

    const tile = screen.getByRole("button", { name: "Pipeline" });
    tile.focus();
    await userEvent.keyboard("{Enter}");

    expect(useWindows.getState().open).toContain("pipeline");
  });

  it("closes on Escape", async () => {
    openLauncher();
    render(<Launcher />);
    expect(useWindows.getState().launcherOpen).toBe(true);

    await userEvent.keyboard("{Escape}");

    expect(useWindows.getState().launcherOpen).toBe(false);
  });
});
