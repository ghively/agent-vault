import { describe, it, expect, afterEach, vi } from "vitest";
import { vaultFetch, ApiError } from "./client";

const res = (body: string, status: number) => new Response(body, { status });

afterEach(() => vi.restoreAllMocks());

describe("vaultFetch error unwrapping", () => {
  async function messageFor(body: string, status = 400): Promise<string> {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(body, status));
    try {
      await vaultFetch("/api/entities/x", { method: "PATCH" });
      throw new Error("expected vaultFetch to throw");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      return (e as ApiError).message;
    }
  }

  it("unwraps a string {detail} into the bare message (no JSON envelope)", async () => {
    const msg = await messageFor(JSON.stringify({ detail: "title must be a non-empty string" }));
    expect(msg).toBe("title must be a non-empty string");
    expect(msg).not.toContain("detail");
    expect(msg).not.toContain("{");
  });

  it("unwraps a 422 array {detail:[{msg}]} into joined messages", async () => {
    const msg = await messageFor(
      JSON.stringify({ detail: [{ loc: ["body", "title"], msg: "field required" }] }),
      422,
    );
    expect(msg).toBe("field required");
  });

  it("unwraps the validation-rollback {message, errors[]} object", async () => {
    const msg = await messageFor(
      JSON.stringify({ detail: { message: "validation failed", errors: ["bad tag", "bad note"] } }),
    );
    expect(msg).toBe("validation failed: bad tag; bad note");
    expect(msg).not.toContain("[object Object]");
  });

  it("falls back to raw text when the body is not JSON", async () => {
    const msg = await messageFor("plain error text");
    expect(msg).toBe("plain error text");
  });
});
