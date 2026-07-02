import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { Desktop } from "./Desktop";
import { useWindows } from "../store/windows";
import { useAuth } from "../store/auth";

function renderDesktop() {
  useAuth.getState().setToken("tok");
  useWindows.getState().reset();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [], counts: {}, env: {} }), { status: 200 }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><Desktop /></QueryClientProvider>);
}

test("vault window is open by default", () => {
  renderDesktop();
  expect(screen.getByText(/vault · vault/)).toBeInTheDocument();
});

test("the dock is removed (no dock launch buttons on the desktop)", () => {
  renderDesktop();
  // The dock previously rendered a titled button per app; it no longer exists.
  expect(screen.queryByTitle("Agent Vault")).not.toBeInTheDocument();
});

test("opening an app from the launcher opens its window", async () => {
  renderDesktop();
  act(() => {
    useWindows.getState().toggleLauncher();
  });
  await userEvent.click(await screen.findByLabelText("Browse"));
  expect(useWindows.getState().open).toContain("browse");
});
