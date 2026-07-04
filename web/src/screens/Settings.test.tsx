import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Settings } from "./Settings";
import { useWindows } from "../store/windows";

const SETTINGS = {
  service: {
    version: "1.2.3",
    vault_path: "/srv/agent-vault/data",
    host: "0.0.0.0",
    port: 7778,
    auth_enabled: true,
  },
  vault: { entity_count: 42, index_built: true },
  compiler: { prompt_contract_version: "pc-7", ollama_model: "llama3" },
  resolvers: {
    default: "env",
    backends: [
      { scheme: "env", module: "resolvers.env", description: "read from process env" },
      { scheme: "op", module: "resolvers.onepassword", description: "1Password CLI" },
    ],
  },
  cadences: ["nightly"],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: false, retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Settings />
    </QueryClientProvider>
  );
}

describe("Settings Screen", () => {
  beforeEach(() => {
    useWindows.getState().reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the loading state before the settings resolve", () => {
    // fetch never settles → query stays pending
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise<Response>(() => {})
    );
    renderSettings();
    expect(screen.getByText("Loading settings…")).toBeInTheDocument();
  });

  it("renders the loaded settings from /api/settings", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(SETTINGS));
    renderSettings();

    // service card
    expect(await screen.findByText("/srv/agent-vault/data")).toBeInTheDocument();
    expect(screen.getByText("1.2.3")).toBeInTheDocument();
    expect(screen.getByText("0.0.0.0:7778")).toBeInTheDocument();
    expect(screen.getByText("token (bearer)")).toBeInTheDocument();

    // vault card
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("built")).toBeInTheDocument();

    // compiler card
    expect(screen.getByText("llama3")).toBeInTheDocument();

    // resolver backends render their scheme + description
    expect(screen.getByText("env://")).toBeInTheDocument();
    expect(screen.getByText("op://")).toBeInTheDocument();
    expect(screen.getByText(/1Password CLI/)).toBeInTheDocument();

    // cadence chip
    expect(screen.getByText("nightly")).toBeInTheDocument();
  });

  it("renders the error state when the request fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response("boom", { status: 500 })
    );
    renderSettings();
    expect(await screen.findByText("Failed to load settings.")).toBeInTheDocument();
  });

  it("drives the UI preference controls through the windows store", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(SETTINGS));
    renderSettings();

    // theme defaults to dark; clicking "light" updates the store
    expect(useWindows.getState().theme).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: "light" }));
    expect(useWindows.getState().theme).toBe("light");

    // density defaults to comfortable; clicking "compact" updates the store
    expect(useWindows.getState().density).toBe("comfortable");
    await userEvent.click(screen.getByRole("button", { name: "compact" }));
    expect(useWindows.getState().density).toBe("compact");
  });

  it("shows the current desktop scale as a percentage", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(SETTINGS));
    useWindows.getState().setDesktopScale(1.2);
    renderSettings();
    await waitFor(() => expect(screen.getByText("120%")).toBeInTheDocument());
  });
});
