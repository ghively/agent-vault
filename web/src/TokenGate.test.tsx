import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { TokenGate } from "./TokenGate";
import { useAuth } from "./store/auth";

// The auth store is a module singleton seeded from sessionStorage; reset it and
// the storage between tests so each starts unauthenticated.
function resetAuth() {
  sessionStorage.clear();
  useAuth.setState({ token: null });
}

function mockHealth(status: number) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
    return { ok: status >= 200 && status < 300, status } as Response;
  });
}

async function enterToken(value: string) {
  await userEvent.type(screen.getByPlaceholderText(/Enter vault token/i), value);
  await userEvent.click(screen.getByRole("button", { name: /Access Vault/i }));
}

describe("TokenGate", () => {
  beforeEach(resetAuth);
  afterEach(() => vi.restoreAllMocks());

  it("renders the token form when unauthenticated", () => {
    render(<TokenGate><div>SECRET APP</div></TokenGate>);
    expect(screen.getByText(/Enter your vault token/i)).toBeInTheDocument();
    expect(screen.queryByText("SECRET APP")).not.toBeInTheDocument();
  });

  it("renders children immediately when a token is already present", () => {
    useAuth.setState({ token: "already-have-it" });
    render(<TokenGate><div>SECRET APP</div></TokenGate>);
    expect(screen.getByText("SECRET APP")).toBeInTheDocument();
    expect(screen.queryByText(/Enter your vault token/i)).not.toBeInTheDocument();
  });

  it("mounts the app after a valid token validates against /api/health", async () => {
    const spy = mockHealth(200);
    render(<TokenGate><div>SECRET APP</div></TokenGate>);
    await enterToken("good-token");
    await waitFor(() => expect(screen.getByText("SECRET APP")).toBeInTheDocument());
    // token stored + health probe carried the bearer header
    expect(useAuth.getState().token).toBe("good-token");
    const [, init] = spy.mock.calls[0];
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer good-token",
    });
  });

  it("reports a 401 as an invalid token and does not mount", async () => {
    mockHealth(401);
    render(<TokenGate><div>SECRET APP</div></TokenGate>);
    await enterToken("bad-token");
    await waitFor(() => expect(screen.getByText(/Invalid token/i)).toBeInTheDocument());
    expect(useAuth.getState().token).toBeNull();
    expect(screen.queryByText("SECRET APP")).not.toBeInTheDocument();
  });

  it("reports a 403 as valid-but-unauthorized", async () => {
    mockHealth(403);
    render(<TokenGate><div>APP</div></TokenGate>);
    await enterToken("scoped-token");
    await waitFor(() =>
      expect(screen.getByText(/not authorized for this vault/i)).toBeInTheDocument());
  });

  it("reports an unexpected status code", async () => {
    mockHealth(500);
    render(<TokenGate><div>APP</div></TokenGate>);
    await enterToken("tok");
    await waitFor(() =>
      expect(screen.getByText(/Unexpected response \(500\)/i)).toBeInTheDocument());
  });

  it("reports a network failure when the service is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    render(<TokenGate><div>APP</div></TokenGate>);
    await enterToken("tok");
    await waitFor(() =>
      expect(screen.getByText(/Can't reach the vault service/i)).toBeInTheDocument());
  });
});
