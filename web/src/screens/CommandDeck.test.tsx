import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { CommandDeck } from "./CommandDeck";

describe("CommandDeck Screen", () => {
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
        <CommandDeck />
      </QueryClientProvider>
    );
    expect(screen.getByText("ASK THE VAULT")).toBeInTheDocument();
  });

  it("shows query chips", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <CommandDeck />
      </QueryClientProvider>
    );
    expect(screen.getByText("what's due this week")).toBeInTheDocument();
    expect(screen.getByText("expiring soon")).toBeInTheDocument();
    expect(screen.getByText("find furnace")).toBeInTheDocument();
  });

  it("shows footer stat tiles", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <CommandDeck />
      </QueryClientProvider>
    );
    expect(screen.getByText("NEXT DUE")).toBeInTheDocument();
    expect(screen.getByText("REVIEW")).toBeInTheDocument();
    expect(screen.getByText("LAST COMPILE")).toBeInTheDocument();
    expect(screen.getByText("VAULT")).toBeInTheDocument();
  });
});
