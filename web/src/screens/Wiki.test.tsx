import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { Wiki } from "./Wiki";

const ROW = {
  slug: "furnace", ts: "asset/appliance", type: "asset", subtype: "appliance",
  status: "compiled", confidence: 0.9, tags: ["hvac"],
};
const DETAIL = {
  slug: "furnace", title: "Furnace", type: "asset", subtype: "appliance",
  status: "compiled", confidence: 0.9, hash: "h",
  prose: "A gas furnace.", sources: [], facts: [{ k: "model", v: "X-90" }], links: [],
};
const SCHEMA = {
  types: [{ type: "asset", subtypes: ["appliance"] }],
  tags: ["hvac", "critical", "seasonal"],
};
const RAW = { path: "entities/asset/furnace.md", content: "---\ntitle: Furnace\n---\nbody\n" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

type Handler = (url: string, init?: RequestInit) => Promise<Response>;

function mockApi(overrides: { patch?: Handler; put?: Handler } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "PATCH") {
      return overrides.patch ? overrides.patch(url, init) : json(DETAIL);
    }
    if (init?.method === "PUT") {
      return overrides.put ? overrides.put(url, init) : json({ ok: true, path: RAW.path });
    }
    if (url.includes("/api/schema")) return json(SCHEMA);
    if (url.includes("/api/entities/furnace/raw")) return json(RAW);
    if (url.includes("/api/entities/furnace")) return json(DETAIL);
    if (url.includes("/api/entities")) return json({ rows: [ROW], total: 1 });
    return new Response("{}", { status: 200 });
  });
}

describe("Wiki Screen", () => {
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

  function renderWiki() {
    return render(
      <QueryClientProvider client={queryClient}>
        <Wiki />
      </QueryClientProvider>
    );
  }

  async function openEntity() {
    await userEvent.click(await screen.findByText("furnace"));
    await screen.findByText("Furnace"); // detail title loaded
  }

  it("renders without crashing", () => {
    renderWiki();
    expect(screen.getByText("Select an entity from the index.")).toBeInTheDocument();
  });

  describe("edit details modal", () => {
    it("prefills title/tags and PATCHes only the changed keys", async () => {
      const patches: { url: string; body: unknown }[] = [];
      mockApi({
        patch: async (url, init) => {
          patches.push({ url, body: JSON.parse(String(init?.body)) });
          return json({ ...DETAIL, title: "Furnace 3000" });
        },
      });
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Edit details" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit details for furnace" });
      expect(dialog).toBeInTheDocument();

      // prefilled title + tags (tags fall back to the entities-list row)
      const title = screen.getByRole("textbox", { name: "title" });
      expect(title).toHaveValue("Furnace");
      expect(screen.getByRole("button", { name: "remove tag hvac" })).toBeInTheDocument();
      // the notes field is blank when the detail exposes no notes fact
      expect(screen.getByRole("textbox", { name: "notes" })).toHaveValue("");

      // change ONLY the title — tags and notes must not be sent
      await userEvent.clear(title);
      await userEvent.type(title, "Furnace 3000");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => expect(patches).toHaveLength(1));
      expect(patches[0].url).toContain("/api/entities/furnace");
      expect(patches[0].body).toEqual({ title: "Furnace 3000" });

      // modal closed; detail cache seeded from the PATCH response
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(await screen.findByText("Furnace 3000")).toBeInTheDocument();
    });

    it("adds tags from the schema vocabulary and sends the tags key when changed", async () => {
      const patches: { body: unknown }[] = [];
      mockApi({
        patch: async (_url, init) => {
          patches.push({ body: JSON.parse(String(init?.body)) });
          return json(DETAIL);
        },
      });
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Edit details" }));
      await screen.findByRole("dialog", { name: "Edit details for furnace" });

      // the add-tag select only offers valid tags not already chosen
      const addTag = await screen.findByRole("combobox", { name: "add tag" });
      expect(screen.queryByRole("option", { name: "hvac" })).not.toBeInTheDocument();
      await userEvent.selectOptions(addTag, "critical");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => expect(patches).toHaveLength(1));
      expect(patches[0].body).toEqual({ tags: ["hvac", "critical"] });
    });

    it("renders server errors verbatim without closing the modal", async () => {
      mockApi({
        patch: async () =>
          new Response("value for 'notes' looks like a secret — rejected", { status: 422 }),
      });
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Edit details" }));
      await screen.findByRole("dialog", { name: "Edit details for furnace" });

      const title = screen.getByRole("textbox", { name: "title" });
      await userEvent.clear(title);
      await userEvent.type(title, "New title");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));

      expect(
        await screen.findByText(/value for 'notes' looks like a secret — rejected/),
      ).toBeInTheDocument();
      expect(screen.getByRole("dialog", { name: "Edit details for furnace" })).toBeInTheDocument();
    });
  });

  describe("raw editor", () => {
    it("loads the file, saves via PUT, and shows a saved indicator", async () => {
      const puts: { url: string; body: unknown }[] = [];
      mockApi({
        put: async (url, init) => {
          puts.push({ url, body: JSON.parse(String(init?.body)) });
          return json({ ok: true, path: RAW.path });
        },
      });
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Open in editor" }));
      const ta = await screen.findByRole("textbox", { name: "raw content of furnace" });
      expect(ta).toHaveValue(RAW.content);
      expect(screen.getByText("entities/asset/furnace.md")).toBeInTheDocument();

      // Save is disabled until the buffer is dirty
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      await userEvent.type(ta, "extra line");
      expect(screen.getByText(/unsaved changes/)).toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(puts).toHaveLength(1));
      expect(puts[0].url).toContain("/api/entities/furnace/raw");
      expect(puts[0].body).toEqual({ content: RAW.content + "extra line" });

      // editor exits back to the detail pane with a saved indicator
      await waitFor(() =>
        expect(screen.queryByRole("textbox", { name: "raw content of furnace" })).not.toBeInTheDocument());
      expect(await screen.findByText(/saved entities\/asset\/furnace\.md/)).toBeInTheDocument();
    });

    it("asks for confirmation before discarding a dirty buffer", async () => {
      mockApi();
      const confirmSpy = vi.spyOn(window, "confirm")
        .mockReturnValueOnce(false)
        .mockReturnValueOnce(true);
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Open in editor" }));
      const ta = await screen.findByRole("textbox", { name: "raw content of furnace" });
      await userEvent.type(ta, "x");

      // confirm → false: stays in the editor
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      expect(confirmSpy).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("textbox", { name: "raw content of furnace" })).toBeInTheDocument();

      // confirm → true: exits without saving
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      await waitFor(() =>
        expect(screen.queryByRole("textbox", { name: "raw content of furnace" })).not.toBeInTheDocument());
    });

    it("keeps the editor open and renders multi-line errors on a rejected save", async () => {
      mockApi({
        put: async () =>
          new Response("slug/type mismatch\nuse Reclassify to move an entity", { status: 400 }),
      });
      renderWiki();
      await openEntity();

      await userEvent.click(screen.getByRole("button", { name: "Open in editor" }));
      const ta = await screen.findByRole("textbox", { name: "raw content of furnace" });
      await userEvent.type(ta, "x");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText(/slug\/type mismatch/)).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "raw content of furnace" })).toBeInTheDocument();
    });
  });
});
