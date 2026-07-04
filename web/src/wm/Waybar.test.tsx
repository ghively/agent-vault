import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Waybar } from "./Waybar";
import { useWindows } from "../store/windows";

const PROPOSALS = { items: [{ id: "a" }, { id: "b" }, { id: "c" }] };
const ENTITIES = { items: [{ ref: "asset/furnace" }, { ref: "asset/router" }] };
const CONFIG = {
  env: { AGENT_VAULT_COMPILER: "ollama" },
  thresholds: { new_tag_min: null, new_subtype_min: null, new_type_locked: false },
  resolvers: [],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/review/proposals")) return json(PROPOSALS);
    if (url.includes("/api/review/entities")) return json(ENTITIES);
    if (url.includes("/api/config")) return json(CONFIG);
    return json({});
  });
}

function renderWaybar() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: false, retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Waybar />
    </QueryClientProvider>
  );
}

describe("Waybar", () => {
  beforeEach(() => {
    useWindows.getState().reset();
    mockApi();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the review, entity, and compiler counters from the API", async () => {
    renderWaybar();
    expect(await screen.findByText(/◈\s*3/)).toBeInTheDocument();
    expect(await screen.findByText(/⚑\s*2/)).toBeInTheDocument();
    expect(await screen.findByText(/⚙\s*ollama/)).toBeInTheDocument();
  });

  it("falls back to the mock compiler label when config has none", async () => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/config")) return json({ env: {}, thresholds: {}, resolvers: [] });
      return json({ items: [] });
    });
    renderWaybar();
    expect(await screen.findByText(/⚙\s*mock/)).toBeInTheDocument();
  });

  it("toggles the launcher when the APPS pill is clicked", async () => {
    renderWaybar();
    expect(useWindows.getState().launcherOpen).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Open application launcher" }));
    expect(useWindows.getState().launcherOpen).toBe(true);
  });

  it("opens the Command Deck app when the assistant button is clicked", async () => {
    renderWaybar();
    expect(useWindows.getState().open).not.toContain("command");
    await userEvent.click(
      screen.getByRole("button", { name: "Open the Command Deck assistant" })
    );
    expect(useWindows.getState().open).toContain("command");
    // opening an app closes the launcher
    expect(useWindows.getState().launcherOpen).toBe(false);
  });

  it("toggles the quick settings panel", async () => {
    renderWaybar();
    expect(useWindows.getState().panelOpen).toBe(false);
    await userEvent.click(
      screen.getByRole("button", { name: "Toggle quick settings panel" })
    );
    expect(useWindows.getState().panelOpen).toBe(true);
  });

  it("focuses an open window via its switcher pill", async () => {
    // 'vault' is open by default; add 'review' and focus back to vault
    useWindows.getState().openApp("review");
    renderWaybar();
    expect(useWindows.getState().open).toEqual(["vault", "review"]);

    await userEvent.click(screen.getByRole("button", { name: "Focus VAULT" }));
    // vault is now the top (focused) window
    const st = useWindows.getState();
    expect(st.z.vault).toBe(st.zTop);
  });
});
