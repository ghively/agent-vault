import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { Review } from "./Review";

describe("Review Screen", () => {
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
        <Review />
      </QueryClientProvider>
    );
    expect(screen.getByText("Human Review: Approve / Reject")).toBeInTheDocument();
  });

  it("shows review queue sections", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Review />
      </QueryClientProvider>
    );
    expect(screen.getByText("loading review queue…")).toBeInTheDocument();
  });
});
