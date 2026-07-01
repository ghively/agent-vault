import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { Browse } from "./Browse";

describe("Browse Screen", () => {
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
        <Browse />
      </QueryClientProvider>
    );
    expect(screen.getByText("Directory Listing: ~/entities/")).toBeInTheDocument();
  });

  it("shows type filter chips", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Browse />
      </QueryClientProvider>
    );
    expect(screen.getByText("all")).toBeInTheDocument();
    expect(screen.getByText("asset")).toBeInTheDocument();
    expect(screen.getByText("account")).toBeInTheDocument();
  });
});
