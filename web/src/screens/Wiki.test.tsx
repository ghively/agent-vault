import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { Wiki } from "./Wiki";

describe("Wiki Screen", () => {
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
        <Wiki />
      </QueryClientProvider>
    );
    expect(screen.getByText("Select an entity from the index.")).toBeInTheDocument();
  });
});
