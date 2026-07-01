import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach } from "vitest";
import { Creds } from "./Creds";

describe("Creds Screen", () => {
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
        <Creds />
      </QueryClientProvider>
    );
    expect(screen.getByText("Credential References")).toBeInTheDocument();
    expect(screen.getByText(/scheme:\/\/store\/path/)).toBeInTheDocument();
  });

  it("shows warning banner", () => {
    render(
      <QueryClientProvider client={queryClient}>
        <Creds />
      </QueryClientProvider>
    );
    expect(screen.getByText(/Plaintext is never stored in the vault/)).toBeInTheDocument();
  });
});
