import { useEffect, useState } from "react";
import { vaultFetch } from "./client";
import { useAuth } from "../store/auth";

export async function startJob(kind: string, slug?: string): Promise<string> {
  const res = await vaultFetch<{ job_id: string }>("/api/jobs/run", {
    method: "POST",
    body: JSON.stringify({ kind, slug }),
  });
  return res.job_id;
}

export async function recompileEntity(slug: string): Promise<string> {
  const res = await vaultFetch<{ job_id: string }>(
    `/api/entities/${encodeURIComponent(slug)}/recompile`,
    { method: "POST", body: JSON.stringify({}) },
  );
  return res.job_id;
}

export interface JobStream {
  lines: string[];
  state: "running" | "done" | "error" | null;
  rc: number | null;
}

export function useJobStream(jobId: string | null): JobStream {
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<JobStream["state"]>(null);
  const [rc, setRc] = useState<number | null>(null);
  useEffect(() => {
    if (!jobId) return;
    setLines([]); setState("running"); setRc(null);
    const ctrl = new AbortController();
    (async () => {
      try {
        const token = useAuth.getState().token;
        const resp = await fetch(`/api/jobs/${jobId}/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: ctrl.signal,
        });
        if (resp.status === 401 || resp.status === 403) useAuth.getState().clearToken();
        if (!resp.ok || !resp.body) { setState("error"); return; }
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          // SSE frames are separated by a blank line; lines are "event: x" / "data: y"
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
            let ev = "message"; let data = "";
            for (const ln of frame.split("\n")) {
              if (ln.startsWith("event:")) ev = ln.slice(6).trim();
              else if (ln.startsWith("data:")) data += ln.slice(5).trim();
            }
            if (ev === "log") setLines((p) => [...p, data]);
            else if (ev === "end") {
              try { const { rc, state } = JSON.parse(data); setRc(rc); setState(state); } catch { setState("error"); }
            }
          }
        }
      } catch (e) {
        if (!ctrl.signal.aborted) setState("error");
      }
    })();
    return () => ctrl.abort();
  }, [jobId]);
  return { lines, state, rc };
}
