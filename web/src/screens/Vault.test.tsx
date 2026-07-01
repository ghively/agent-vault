import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { Vault } from "./Vault";

describe("Vault Screen", () => {
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

  it("renders hub without crashing", () => {
    const onNavigate = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <Vault onNavigate={onNavigate} />
      </QueryClientProvider>
    );
    expect(screen.getByText("Agent Vault")).toBeInTheDocument();
    expect(screen.getByText("BROWSE")).toBeInTheDocument();
    expect(screen.getByText("WIKI")).toBeInTheDocument();
  });

  it("shows all hub tiles", () => {
    const onNavigate = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <Vault onNavigate={onNavigate} />
      </QueryClientProvider>
    );
    expect(screen.getByText("search and filter vault entities")).toBeInTheDocument();
    expect(screen.getByText("read and navigate the household wiki")).toBeInTheDocument();
    expect(screen.getByText("ingest → compile → promote pipeline")).toBeInTheDocument();
    expect(screen.getByText("approve or reject pending items")).toBeInTheDocument();
    expect(screen.getByText("credential references and resolvers")).toBeInTheDocument();
    expect(screen.getByText("query the vault with natural language")).toBeInTheDocument();
  });
});
