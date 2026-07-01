import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import App from "./App";

describe("App", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          refetchOnWindowFocus: false,
          retry: false,
        },
      },
    });
  });

  it("renders the sidebar navigation", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    );
    const agentVaultElements = screen.getAllByText("Agent Vault");
    expect(agentVaultElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Browse")).toBeInTheDocument();
    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("Vault")).toBeInTheDocument();
    expect(screen.getByText("Credentials")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Pipeline")).toBeInTheDocument();
    expect(screen.getByText("Command Deck")).toBeInTheDocument();
  });

  it("renders the Vault hub screen by default", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    );
    // Vault hub should be visible by default
    const agentVaultElements = screen.getAllByText("Agent Vault");
    expect(agentVaultElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("BROWSE")).toBeInTheDocument();
    expect(screen.getByText("WIKI")).toBeInTheDocument();
  });
});
