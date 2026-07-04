import { vaultFetch, apiUrl } from "./client";
import { useAuth } from "../store/auth";

/** Start a background job. Contract: POST /api/jobs/run {op, args} -> {job_id}. */
export async function startJob(op: string, args: string[] = []): Promise<string> {
  const res = await vaultFetch<{ job_id: string }>("/api/jobs/run", {
    method: "POST",
    body: JSON.stringify({ op, args }),
  });
  return res.job_id;
}

/**
 * Recompile a single entity. The endpoint is SYNCHRONOUS: it returns the
 * compile summary dict directly (no job_id / no SSE stream).
 */
export async function recompileEntity(slug: string): Promise<Record<string, unknown>> {
  return vaultFetch<Record<string, unknown>>(
    `/api/entities/${encodeURIComponent(slug)}/recompile`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

// ── SSE parsing ───────────────────────────────────────────────────────────────
//
// sse-starlette frames use \r\n line endings and \r\n\r\n frame separators;
// we also tolerate plain \n / \n\n. Multi-line `data:` fields are joined with
// \n per the SSE spec, and data is NOT trimmed (log indentation matters).

export interface SSEParser {
  /** Feed a decoded chunk; fires onEvent for every complete frame found. */
  push(chunk: string): void;
}

export function createSSEParser(
  onEvent: (event: string, data: string) => void,
): SSEParser {
  let buf = "";
  return {
    push(chunk: string) {
      buf += chunk;
      for (;;) {
        // Find the earliest frame separator — \r\n\r\n or \n\n.
        const crlf = buf.indexOf("\r\n\r\n");
        const lf = buf.indexOf("\n\n");
        let idx = -1;
        let sepLen = 0;
        if (crlf >= 0 && (lf < 0 || crlf <= lf)) { idx = crlf; sepLen = 4; }
        else if (lf >= 0) { idx = lf; sepLen = 2; }
        if (idx < 0) break;

        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + sepLen);

        let event = "message";
        const dataLines: string[] = [];
        for (let line of frame.split("\n")) {
          if (line.endsWith("\r")) line = line.slice(0, -1);
          if (line.startsWith("event:")) {
            event = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            // Per SSE spec: strip the field name and ONE leading space; do not trim.
            let value = line.slice(5);
            if (value.startsWith(" ")) value = value.slice(1);
            dataLines.push(value);
          }
        }
        if (dataLines.length > 0 || event !== "message") {
          onEvent(event, dataLines.join("\n"));
        }
      }
    },
  };
}

export interface JobResult {
  state: "done" | "error";
  rc: number | null;
}

/**
 * Stream a job's SSE output. Calls onLine for every "stdout"/"stderr" log line
 * (stderr lines are prefixed so they're distinguishable) and resolves when the
 * terminal "end" event arrives with data = {"status": "completed"|"failed",
 * "returncode": int|null}. Resolves {state:"error"} on transport failure or if
 * the stream closes without an "end" event.
 */
export async function streamJob(
  jobId: string,
  onLine: (line: string) => void,
  signal?: AbortSignal,
): Promise<JobResult> {
  const token = useAuth.getState().token;
  let resp: Response;
  try {
    resp = await fetch(apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/stream`), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    });
  } catch {
    return { state: "error", rc: null };
  }
  // 401 = bad/missing token -> clear it. 403 = valid but unscoped -> keep it
  // (matches the policy documented in client.ts).
  if (resp.status === 401) useAuth.getState().clearToken();
  if (!resp.ok || !resp.body) return { state: "error", rc: null };

  let result: JobResult | null = null;
  const parser = createSSEParser((event, data) => {
    if (event === "stdout") {
      onLine(data);
    } else if (event === "stderr") {
      onLine(`[stderr] ${data}`);
    } else if (event === "end") {
      try {
        const { status, returncode } = JSON.parse(data) as {
          status: "completed" | "failed";
          returncode: number | null;
        };
        result = {
          state: status === "completed" ? "done" : "error",
          rc: returncode ?? null,
        };
      } catch {
        result = { state: "error", rc: null };
      }
    }
  });

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(dec.decode(value, { stream: true }));
      if (result) break;
    }
  } catch {
    return result ?? { state: "error", rc: null };
  }
  // Stream closed without a terminal "end" event => treat as an error.
  return result ?? { state: "error", rc: null };
}
