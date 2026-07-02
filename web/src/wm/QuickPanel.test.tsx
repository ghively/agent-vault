import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { QuickPanel } from "./QuickPanel";
import { useAuth } from "../store/auth";

test("quick panel shows the compile backend from config", async () => {
  useAuth.getState().setToken("tok");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ env: { AGENT_VAULT_COMPILER: "ollama" }, thresholds: {}, resolvers: [] }), { status: 200 }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><QuickPanel /></QueryClientProvider>);
  expect(await screen.findByText(/ollama/)).toBeInTheDocument();
});
