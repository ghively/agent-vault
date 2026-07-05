import { useAuth } from "../store/auth";

// Empty/unset => same-origin (relative /api) — correct when the UI is served
// from the vault API service itself (e.g. http://<host>:7778/). Set
// VITE_VAULT_API_URL only for dev against a separate backend.
const VAULT_API_URL = import.meta.env.VITE_VAULT_API_URL ?? "";

/**
 * Resolve an API path against VAULT_API_URL so remote-API deployments work.
 * Every fetch that bypasses vaultFetch (SSE streams, TokenGate's health probe)
 * must build its URL with this instead of using a bare relative path.
 */
export function apiUrl(path: string): string {
  return path.startsWith("/api") ? `${VAULT_API_URL}${path}` : `${VAULT_API_URL}/api${path}`;
}

/**
 * Host the API is served from, for display purposes (e.g. the Command Deck
 * endpoint pill). Falls back to the page's own host when VAULT_API_URL is
 * unset (same-origin deployment).
 */
export function apiHost(): string {
  if (VAULT_API_URL) {
    try {
      return new URL(VAULT_API_URL).host;
    } catch {
      return VAULT_API_URL;
    }
  }
  return typeof window !== "undefined" ? window.location.host : "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const cap = (s: string): string => (s.length > 300 ? s.slice(0, 300) + "…" : s);

/**
 * Read an error body safely: never surface a raw HTML error page, always cap
 * length, and unwrap FastAPI's `{"detail": …}` envelope so the user sees the
 * message rather than the literal JSON. `detail` may be a string, a
 * `{message, errors[]}` object (edit.py's validation-rollback), or a 422 array
 * of `{loc, msg}` items.
 */
async function errorMessage(res: Response): Promise<string> {
  let text: string;
  try {
    text = (await res.text()).trim();
  } catch {
    return `request failed (${res.status})`;
  }
  if (!text || text.startsWith("<")) return `request failed (${res.status})`;
  try {
    const detail = (JSON.parse(text) as { detail?: unknown })?.detail;
    if (typeof detail === "string" && detail.trim()) return cap(detail.trim());
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((e) => (e && typeof (e as { msg?: unknown }).msg === "string"
          ? (e as { msg: string }).msg
          : String(e)))
        .filter(Boolean);
      if (msgs.length) return cap(msgs.join("; "));
    } else if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; errors?: unknown };
      if (typeof d.message === "string") {
        const errs = Array.isArray(d.errors) ? d.errors.map(String).filter(Boolean) : [];
        return cap(errs.length ? `${d.message}: ${errs.join("; ")}` : d.message);
      }
    }
  } catch {
    // Body wasn't JSON — fall through to the raw (capped) text.
  }
  return cap(text);
}

export async function vaultFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = useAuth.getState().token;
  const url = apiUrl(path);
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers || {}),
      },
    });
  } catch {
    // Network/CORS failure — fetch rejects with a bare TypeError. Normalize it.
    throw new ApiError(0, "network error — is the server reachable?");
  }
  // 401 = no/expired/invalid token → drop it and re-auth. A 403 is an
  // authorization failure (e.g. a scoped key lacking a role): the token is still
  // valid, so do NOT clear it — clearing bounced the user back to the login gate
  // and re-mounted the password field, retriggering password-manager save prompts.
  if (res.status === 401) {
    useAuth.getState().clearToken();
    throw new ApiError(401, "unauthorized");
  }
  if (res.status === 403) {
    throw new ApiError(403, "forbidden — your key lacks permission for this action");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}
