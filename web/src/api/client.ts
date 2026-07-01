import { useAuth } from "../store/auth";

const VAULT_API_URL = import.meta.env.VITE_VAULT_API_URL || "http://127.0.0.1:7778";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Read an error body safely: never surface a raw HTML error page, always cap length. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const text = (await res.text()).trim();
    if (!text || text.startsWith("<")) return `request failed (${res.status})`;
    return text.length > 300 ? text.slice(0, 300) + "…" : text;
  } catch {
    return `request failed (${res.status})`;
  }
}

export async function vaultFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = useAuth.getState().token;
  const url = path.startsWith("/api") ? `${VAULT_API_URL}${path}` : `${VAULT_API_URL}/api${path}`;
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
