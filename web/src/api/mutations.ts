import { useMutation, useQueryClient } from "@tanstack/react-query";
import { vaultFetch } from "./client";

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

export function useApplyConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { env?: Record<string, string>; thresholds?: Record<string, number> }) =>
      vaultFetch("/api/config/apply", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
}
