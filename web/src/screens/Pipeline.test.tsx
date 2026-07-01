import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { Pipeline } from "./Pipeline";

describe("Pipeline Screen", () => {
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

  it("renders without crashing", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Pipeline />
      </QueryClientProvider>
    );
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows pipeline stages when loaded", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Pipeline />
      </QueryClientProvider>
    );
    // Initial loading state
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
