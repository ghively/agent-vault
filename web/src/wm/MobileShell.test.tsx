import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { MobileShell } from "./MobileShell";
import { useWindows } from "../store/windows";
import { useAuth } from "../store/auth";

function renderMobileShell() {
  useAuth.getState().setToken("tok");
  useWindows.getState().reset();
  // Close all windows initially
  useWindows.getState().close("vault");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [], counts: {}, env: {} }), { status: 200 }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MobileShell /></QueryClientProvider>);
}

describe("MobileShell", () => {
  it("renders empty state when no apps are open", () => {
    renderMobileShell();
    expect(screen.getByText("AGENT VAULT")).toBeInTheDocument();
    expect(screen.getByText(/Tap ☰ to open an application/)).toBeInTheDocument();
  });

  it("renders hamburger menu button", () => {
    renderMobileShell();
    const menuButton = screen.getByLabelText("Toggle app drawer");
    expect(menuButton).toBeInTheDocument();
    expect(menuButton).toHaveTextContent("☰");
  });

  it("opens app drawer when hamburger is clicked", async () => {
    const user = userEvent.setup();
    renderMobileShell();

    const menuButton = screen.getByLabelText("Toggle app drawer");
    await user.click(menuButton);

    // Should show app drawer with apps
    expect(screen.getByText("Browse")).toBeInTheDocument();
    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();

    // Menu button should now show X
    expect(menuButton).toHaveTextContent("✕");
  });

  it("closes drawer when clicking outside", async () => {
    const user = userEvent.setup();
    renderMobileShell();

    // Open drawer
    const menuButton = screen.getByLabelText("Toggle app drawer");
    await user.click(menuButton);
    expect(screen.getByText("Browse")).toBeInTheDocument();

    // Click overlay to close
    const overlay = screen.getByText("Browse").closest("div")?.previousElementSibling;
    if (overlay) {
      await user.click(overlay);
    }

    // Drawer should be closed
    expect(screen.queryByText("Browse")).not.toBeInTheDocument();
  });

  it("does not show close button when no app is open", () => {
    renderMobileShell();
    expect(screen.queryByText("Close")).not.toBeInTheDocument();
  });

  it("shows multiple apps in drawer from APP_ORDER", async () => {
    const user = userEvent.setup();
    renderMobileShell();

    // Open drawer
    const menuButton = screen.getByLabelText("Toggle app drawer");
    await user.click(menuButton);

    // Check for a sample of apps from different categories
    expect(screen.getByText("Browse")).toBeInTheDocument();
    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Vault")).toBeInTheDocument();
    expect(screen.getByText("Credentials")).toBeInTheDocument();
  });
});
