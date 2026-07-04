import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Review } from "./Review";

const PROPOSAL = { id: "abcd1234", kind: "tag", concept: "hvac", sightings: 3, reason: "seen in 3 sources" };
const ENTITY = { ref: "asset/furnace", title: "Furnace", confidence: 0.4, gated: false, inferred: true };

function mockApi(post: () => Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") return post();
    if (url.includes("/api/review/proposals")) {
      return new Response(JSON.stringify({ items: [PROPOSAL] }), { status: 200 });
    }
    if (url.includes("/api/review/entities")) {
      return new Response(JSON.stringify({ items: [ENTITY] }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
}

describe("Review Screen", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          refetchOnWindowFocus: false,
          retry: false,
        },
        mutations: { retry: false },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
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

  it("disables approve/reject while a mutation is pending (no double-submit)", async () => {
    // POST never settles — the mutation stays pending
    mockApi(() => new Promise<Response>(() => {}));
    render(
      <QueryClientProvider client={queryClient}>
        <Review />
      </QueryClientProvider>
    );
    await screen.findByText("hvac"); // proposal loaded

    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    // Entity card renders in the left column, proposal in the right.
    await userEvent.click(approveButtons[1]);

    await waitFor(() => {
      expect(approveButtons[1]).toBeDisabled();
    });
    const rejectButtons = screen.getAllByRole("button", { name: "Reject" });
    expect(rejectButtons[1]).toBeDisabled();
  });

  it("renders a visible error when a mutation fails", async () => {
    mockApi(async () => new Response("lock held by another operation", { status: 503 }));
    render(
      <QueryClientProvider client={queryClient}>
        <Review />
      </QueryClientProvider>
    );
    await screen.findByText("Furnace"); // entity loaded

    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    await userEvent.click(approveButtons[0]); // entity approve

    expect(await screen.findByText(/error — lock held by another operation/)).toBeInTheDocument();
    // buttons are re-enabled after the failure so the user can retry
    await waitFor(() => expect(approveButtons[0]).toBeEnabled());
  });
});
