import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, afterEach, vi } from "vitest";
import { useSchema, useEntityRaw } from "./hooks";
import { useReclassifyEntity, usePatchEntity, useSaveEntityRaw } from "./mutations";

const SCHEMA = {
  types: [{ type: "asset", subtypes: ["appliance", "vehicle"] }],
  tags: ["hvac", "critical"],
};
const RAW = { path: "entities/asset/furnace.md", content: "---\ntitle: Furnace\n---\n" };

function wrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSchema", () => {
  it("fetches /api/schema and returns types + tags", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(SCHEMA));
    const { result } = renderHook(() => useSchema(), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(spy.mock.calls[0][0])).toContain("/api/schema");
    expect(result.current.data).toEqual(SCHEMA);
    expect(result.current.data?.types[0].subtypes).toContain("vehicle");
  });
});

describe("useEntityRaw", () => {
  it("fetches the raw file when enabled", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(RAW));
    const { result } = renderHook(() => useEntityRaw("furnace", true), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(spy.mock.calls[0][0])).toContain("/api/entities/furnace/raw");
    expect(result.current.data).toEqual(RAW);
  });

  it("does not fetch when disabled", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(RAW));
    renderHook(() => useEntityRaw("furnace", false), { wrapper: wrapper() });
    await new Promise((r) => setTimeout(r, 10));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useReclassifyEntity", () => {
  it("POSTs the reclassify body (reason omitted when empty) and returns the response", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({
      slug: "furnace", from: "asset/appliance", to: "property/home",
      moved: true, refs_rewritten: 2, detail: "moved",
    }));
    const { result } = renderHook(() => useReclassifyEntity(), { wrapper: wrapper() });

    result.current.mutate({ slug: "furnace", to_type: "property", to_subtype: "home" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/api/entities/furnace/reclassify");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ to_type: "property", to_subtype: "home" });
    expect(result.current.data?.refs_rewritten).toBe(2);
    expect(result.current.data?.moved).toBe(true);
  });
});

describe("usePatchEntity", () => {
  it("PATCHes only the provided keys", async () => {
    const detail = {
      slug: "furnace", title: "New", type: "asset", subtype: "appliance",
      status: "compiled", confidence: 1, hash: "h",
      prose: "", sources: [], facts: [], links: [],
    };
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(detail));
    const { result } = renderHook(() => usePatchEntity(), { wrapper: wrapper() });

    result.current.mutate({ slug: "furnace", body: { title: "New" } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/api/entities/furnace");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ title: "New" });
    expect(result.current.data?.title).toBe("New");
  });
});

describe("useSaveEntityRaw", () => {
  it("PUTs the content and returns ok + path", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ok: true, path: RAW.path }));
    const { result } = renderHook(() => useSaveEntityRaw(), { wrapper: wrapper() });

    result.current.mutate({ slug: "furnace", content: "new content" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("/api/entities/furnace/raw");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ content: "new content" });
    expect(result.current.data).toEqual({ ok: true, path: RAW.path });
  });
});
