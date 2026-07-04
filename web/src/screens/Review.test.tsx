import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Review } from "./Review";

const PROPOSAL = { id: "abcd1234", kind: "tag", concept: "hvac", sightings: 3, reason: "seen in 3 sources" };
const ENTITY = { ref: "asset/furnace", title: "Furnace", confidence: 0.4, gated: false, inferred: true };
const SCHEMA = {
  types: [
    { type: "asset", subtypes: ["appliance", "vehicle"] },
    { type: "property", subtypes: ["home"] },
  ],
  tags: ["hvac"],
};
const DETAIL = {
  slug: "furnace", title: "Furnace", type: "asset", subtype: "appliance",
  status: "needs_review", confidence: 0.4, hash: "h",
  prose: "", sources: [], facts: [], links: [],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function mockApi(post: (url: string, init?: RequestInit) => Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") return post(url, init);
    if (url.includes("/api/review/proposals")) return json({ items: [PROPOSAL] });
    if (url.includes("/api/review/entities")) return json({ items: [ENTITY] });
    if (url.includes("/api/schema")) return json(SCHEMA);
    if (url.includes("/api/entities/furnace")) return json(DETAIL);
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

  describe("reclassify dialog", () => {
    function renderReview() {
      return render(
        <QueryClientProvider client={queryClient}>
          <Review />
        </QueryClientProvider>
      );
    }

    async function openDialog() {
      await screen.findByText("Furnace");
      await userEvent.click(screen.getByRole("button", { name: "Reclassify" }));
      return screen.findByRole("dialog", { name: "Reclassify asset/furnace" });
    }

    it("opens, populates the selects from /api/schema, and disables Confirm for an unchanged target", async () => {
      mockApi(async () => json({}));
      renderReview();
      await openDialog();

      const typeSelect = await screen.findByRole("combobox", { name: "target type" });
      // types from the schema
      expect(await screen.findByRole("option", { name: "property" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "asset" })).toBeInTheDocument();

      const confirm = screen.getByRole("button", { name: "Confirm" });
      expect(confirm).toBeDisabled(); // nothing selected yet

      // same type + same subtype as the current classification → still disabled
      await userEvent.selectOptions(typeSelect, "asset");
      const subtypeSelect = screen.getByRole("combobox", { name: "target subtype" });
      // subtype options follow the selected type
      expect(screen.getByRole("option", { name: "appliance" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "vehicle" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "home" })).not.toBeInTheDocument();
      await userEvent.selectOptions(subtypeSelect, "appliance");
      expect(confirm).toBeDisabled();

      // a genuinely different target enables Confirm
      await userEvent.selectOptions(typeSelect, "property");
      await userEvent.selectOptions(
        screen.getByRole("combobox", { name: "target subtype" }),
        "home",
      );
      expect(confirm).toBeEnabled();

      // Escape closes the dialog
      await userEvent.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("submits the correct body and shows the inline confirmation", async () => {
      const posts: { url: string; body: unknown }[] = [];
      mockApi(async (url, init) => {
        posts.push({ url, body: JSON.parse(String(init?.body)) });
        return json({
          slug: "furnace", from: "asset/appliance", to: "property/home",
          moved: true, refs_rewritten: 3, detail: "moved",
        });
      });
      renderReview();
      await openDialog();

      await userEvent.selectOptions(
        await screen.findByRole("combobox", { name: "target type" }),
        "property",
      );
      await userEvent.selectOptions(
        screen.getByRole("combobox", { name: "target subtype" }),
        "home",
      );
      await userEvent.type(screen.getByRole("textbox", { name: "reason" }), "wrong bucket");
      await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

      await waitFor(() => expect(posts).toHaveLength(1));
      expect(posts[0].url).toContain("/api/entities/furnace/reclassify");
      expect(posts[0].body).toEqual({
        to_type: "property",
        to_subtype: "home",
        reason: "wrong bucket",
      });

      // dialog closes; the inline confirmation appears in the entities column
      expect(
        await screen.findByText(/furnace re-filed to property\/home, 3 refs rewritten/),
      ).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("renders a 409 error inline without closing the dialog", async () => {
      mockApi(async () =>
        new Response("ledger blocks this move: pending proposal", { status: 409 }));
      renderReview();
      await openDialog();

      await userEvent.selectOptions(
        await screen.findByRole("combobox", { name: "target type" }),
        "property",
      );
      await userEvent.selectOptions(
        screen.getByRole("combobox", { name: "target subtype" }),
        "home",
      );
      await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

      expect(
        await screen.findByText(/ledger blocks this move: pending proposal/),
      ).toBeInTheDocument();
      expect(screen.getByRole("dialog", { name: "Reclassify asset/furnace" })).toBeInTheDocument();
    });
  });
});
