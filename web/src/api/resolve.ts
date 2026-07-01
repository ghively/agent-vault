import { vaultFetch } from "./client";

export async function resolveSecret(slug: string, resolveToken: string): Promise<string> {
  const res = await vaultFetch<{ ok: boolean; secret: string }>(
    `/api/creds/${encodeURIComponent(slug)}/resolve`,
    { method: "POST", headers: { "X-Resolve-Token": resolveToken }, body: "{}" },
  );
  return res.secret;
}
