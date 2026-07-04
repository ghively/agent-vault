import { vaultFetch } from "./client";

/**
 * Resolve a credential reference to its plaintext secret. Authenticated by the
 * normal bearer token only — the backend does not read any extra header, so
 * asking the user to "re-enter" a token here would be security theater.
 */
export async function resolveSecret(slug: string): Promise<string> {
  const res = await vaultFetch<{ ok: boolean; secret: string }>(
    `/api/creds/${encodeURIComponent(slug)}/resolve`,
    { method: "POST", body: "{}" },
  );
  return res.secret;
}
