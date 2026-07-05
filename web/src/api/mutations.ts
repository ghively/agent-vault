import { useMutation, useQueryClient } from "@tanstack/react-query";
import { vaultFetch } from "./client";
import type { EntityDetail, EntityPatchBody, ReclassifyResponse, SaveRawResponse } from "./types";

function useReviewMutation(makePath: (v: { id?: string; ref?: string; reason?: string }) => string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id?: string; ref?: string; reason?: string }) =>
      vaultFetch(makePath(v), { method: "POST", body: JSON.stringify({ reason: v.reason || "" }) }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["review", "proposals"] }),
        qc.invalidateQueries({ queryKey: ["review", "entities"] }),
        qc.invalidateQueries({ queryKey: ["status"] }),
      ]);
    },
  });
}

// Entity refs are "type/slug": the backend route is {ref:path}, so the '/'
// must stay literal — encode each segment individually (protects '#'/'?'/'%').
const encodeRef = (ref: string) => ref.split("/").map(encodeURIComponent).join("/");

export const useApproveProposal = () => useReviewMutation((v) => `/api/review/proposals/${encodeURIComponent(v.id ?? "")}/approve`);
export const useRejectProposal = () => useReviewMutation((v) => `/api/review/proposals/${encodeURIComponent(v.id ?? "")}/reject`);
export const useApproveEntity = () => useReviewMutation((v) => `/api/review/entities/${encodeRef(v.ref ?? "")}/approve`);
export const useRejectEntity = () => useReviewMutation((v) => `/api/review/entities/${encodeRef(v.ref ?? "")}/reject`);

/**
 * POST /api/entities/{slug}/reclassify — move an entity to a different
 * (type, subtype). `reason` is optional and omitted when empty.
 */
export function useReclassifyEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { slug: string; to_type: string; to_subtype: string; reason?: string }) =>
      vaultFetch<ReclassifyResponse>(
        `/api/entities/${encodeURIComponent(v.slug)}/reclassify`,
        {
          method: "POST",
          body: JSON.stringify({
            to_type: v.to_type,
            to_subtype: v.to_subtype,
            ...(v.reason ? { reason: v.reason } : {}),
          }),
        },
      ),
    onSuccess: async (_data, v) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["review", "entities"] }),
        qc.invalidateQueries({ queryKey: ["entities"] }),
        qc.invalidateQueries({ queryKey: ["status"] }),
        qc.invalidateQueries({ queryKey: ["entity", v.slug] }),
      ]);
    },
  });
}

/**
 * PATCH /api/entities/{slug} — partial edit (title/tags/notes). The response
 * is the full updated EntityDetail, so seed the detail cache directly.
 */
export function usePatchEntity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { slug: string; body: EntityPatchBody }) =>
      vaultFetch<EntityDetail>(
        `/api/entities/${encodeURIComponent(v.slug)}`,
        { method: "PATCH", body: JSON.stringify(v.body) },
      ),
    onSuccess: async (detail, v) => {
      qc.setQueryData(["entity", v.slug], detail);
      await Promise.all([
        // PATCH rewrote the entity's markdown file, so the raw editor copy
        // (["entity", slug, "raw"]) is stale — invalidate it so "Open in editor"
        // reloads the just-saved content instead of showing (and then clobbering
        // with) a pre-edit copy. The detail cache is seeded above, so only the
        // raw sub-key needs invalidating here.
        qc.invalidateQueries({ queryKey: ["entity", v.slug, "raw"] }),
        qc.invalidateQueries({ queryKey: ["entities"] }),
      ]);
    },
  });
}

/** PUT /api/entities/{slug}/raw — overwrite the entity's markdown file. */
export function useSaveEntityRaw() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { slug: string; content: string }) =>
      vaultFetch<SaveRawResponse>(
        `/api/entities/${encodeURIComponent(v.slug)}/raw`,
        { method: "PUT", body: JSON.stringify({ content: v.content }) },
      ),
    onSuccess: async (_data, v) => {
      await Promise.all([
        // Also matches ["entity", slug, "raw"], keeping the raw copy fresh.
        qc.invalidateQueries({ queryKey: ["entity", v.slug] }),
        qc.invalidateQueries({ queryKey: ["entities"] }),
      ]);
    },
  });
}

export function useApplyConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { env?: Record<string, string>; thresholds?: Record<string, number> }) =>
      vaultFetch("/api/config/apply", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["config"] }),
        // /api/settings echoes the same compiler config (model, thresholds), so
        // refresh the settings overview too or it shows the pre-apply values.
        qc.invalidateQueries({ queryKey: ["settings"] }),
      ]);
    },
  });
}
