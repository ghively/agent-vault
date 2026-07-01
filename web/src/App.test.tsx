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
    expect(screen.getByText("Agent Vault")).toBeInTheDocument();
    expect(screen.getAllByText("Browse")).toHaveLength(2); // Nav + content
    expect(screen.getByText("Wiki")).toBeInTheDocument();
    expect(screen.getByText("Vault")).toBeInTheDocument();
    expect(screen.getByText("Credentials")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Pipeline")).toBeInTheDocument();
    expect(screen.getByText("Command Deck")).toBeInTheDocument();
  });

  it("renders the Browse screen by default", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    );
    // Check that Browse appears in both nav and content
    const browseElements = screen.getAllByText("Browse");
    expect(browseElements).toHaveLength(2);
    // The second one should be in the main content area
    expect(browseElements[1].textContent).toBe("Browse");
  });
});
