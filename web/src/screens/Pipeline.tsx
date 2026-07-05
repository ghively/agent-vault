import React, { useState, useRef, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRuns, useLedgers } from "../api/hooks";
import { startJob, streamJob, cancelJob } from "../api/jobs";
import { C, FONT_MONO, FONT_UI } from "../theme";
import type { Run } from "../api/types";

// The weekly cadence stages, in cadences/weekly.sh order (ingest → compile →
// promote → validate). "validate" is not an allowed job op, so the client
// chains the three exposed stages; validation still runs inside the shell
// cadence on the host.
const WEEKLY_OPS = ["ingest", "compile", "promote"] as const;

// Abort a job whose SSE stream goes silent for this long (a hung/never-ending
// stage) so the UI can't get stuck "running" forever (F2).
const STALL_MS = 120_000;

const STAGES = [
  {
    name: "INGEST",
    script: "ingest.sh",
    stat: "raw sources → discovery/",
    color: C.cyan,
    border: "rgba(0,243,255,0.35)",
    llm: "",
    llmColor: "transparent",
  },
  {
    name: "COMPILE",
    script: "compile.sh",
    stat: "discovery/ → entities/",
    color: C.greenSoft,
    border: "rgba(0,255,0,0.3)",
    llm: "LLM: extract + normalise",
    llmColor: C.purple,
  },
  {
    name: "PROMOTE",
    script: "promote.sh",
    stat: "proposals → manifest",
    color: C.amber,
    border: "rgba(255,189,46,0.35)",
    llm: "LLM: propose only",
    llmColor: C.purple,
  },
  {
    name: "REGISTRY",
    script: "registry.sh",
    stat: "manifest → registry/",
    color: C.text,
    border: "rgba(204,255,204,0.2)",
    llm: "deterministic only",
    llmColor: C.greenSoft,
  },
] as const;

function rcColor(rc: number): string {
  return rc === 0 ? C.greenSoft : C.red;
}

function fmtDuration(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

export function Pipeline() {
  const { data: runsData, isError: runsError } = useRuns();
  const { data: ledgers, isError: ledgersError } = useLedgers();
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [jobLabel, setJobLabel] = useState<string | null>(null);
  const [jobState, setJobState] = useState<"running" | "done" | "error" | null>(null);
  const [jobRc, setJobRc] = useState<number | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const currentJobIdRef = useRef<string | null>(null);
  const cancelledRef = useRef(false);
  // Abort any in-flight stream when the window/component unmounts so the fetch
  // doesn't keep running in the background (F2).
  useEffect(() => () => {
    mountedRef.current = false;
    abortRef.current?.abort();
  }, []);

  /** Run ops in sequence; stop (and report which stage failed) on the first
   *  job whose SSE "end" is not completed/rc 0. Abortable + stall-guarded. */
  async function runOps(label: string, ops: readonly string[]) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    cancelledRef.current = false;
    setRunning(true);
    setErrorMsg(null);
    setJobLabel(label);
    setJobState("running");
    setJobRc(null);
    setLines([]);

    // Stall watchdog: abort if no SSE line arrives for STALL_MS. Reset on every
    // line and at the start of each stage.
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    const armStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => { cancelledRef.current = false; ctrl.abort(); }, STALL_MS);
    };
    const append = (l: string) => {
      armStall();
      if (mountedRef.current) setLines((p) => [...p, l]);
    };

    try {
      for (const op of ops) {
        if (ops.length > 1) append(`── stage: ${op} ──`);
        armStall();
        const id = await startJob(op, []);
        currentJobIdRef.current = id;
        const res = await streamJob(id, append, ctrl.signal);
        currentJobIdRef.current = null;
        if (!mountedRef.current) return;
        setJobRc(res.rc);
        if (res.state !== "done" || (res.rc ?? 1) !== 0) {
          setJobState("error");
          if (ctrl.signal.aborted) {
            setErrorMsg(cancelledRef.current
              ? `run cancelled at stage "${op}"`
              : `stage "${op}" stalled (no output for ${STALL_MS / 1000}s) — aborted`);
          } else if (res.message) {
            // Transport/auth failure (e.g. 403 forbidden) — show the reason.
            setErrorMsg(`${op} failed — ${res.message}`);
          } else {
            setErrorMsg(
              ops.length > 1
                ? `weekly run failed at stage "${op}"${res.rc != null ? ` (rc=${res.rc})` : ""} — later stages skipped`
                : `${op} failed${res.rc != null ? ` (rc=${res.rc})` : ""}`,
            );
          }
          return;
        }
      }
      if (mountedRef.current) setJobState("done");
    } catch (e) {
      if (!mountedRef.current) return;
      setJobState("error");
      setErrorMsg(e instanceof Error ? e.message : "failed to start the job");
    } finally {
      clearTimeout(stallTimer);
      abortRef.current = null;
      currentJobIdRef.current = null;
      if (mountedRef.current) setRunning(false);
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["ledgers"] });
      // A compile/promote job changes entity status + the review queue; refresh
      // the dashboard/review-driven queries too, not just runs/ledgers.
      qc.invalidateQueries({ queryKey: ["status"] });
      qc.invalidateQueries({ queryKey: ["review"] });
      // The settings overview echoes entity_count / index_built, which a run
      // changes — refresh it too.
      qc.invalidateQueries({ queryKey: ["settings"] });
    }
  }

  function handleCancel() {
    cancelledRef.current = true;
    abortRef.current?.abort();
    // Aborting only stops the client reading the stream; tell the server to
    // terminate the subprocess too, otherwise it runs to completion.
    const id = currentJobIdRef.current;
    if (id) void cancelJob(id);
  }

  function handleRunWeekly() {
    if (!window.confirm("Start the weekly cadence run?")) return;
    void runOps("weekly (ingest → compile → promote)", WEEKLY_OPS);
  }

  function handleDryRun() {
    void runOps("lint (dry run)", ["lint"]);
  }

  if (runsError || ledgersError) {
    return (
      <div style={{ padding: 20, color: C.red, fontFamily: FONT_MONO, fontSize: 12 }}>
        error — could not reach the pipeline API (/api/runs, /api/ledgers)
      </div>
    );
  }

  if (!runsData || !ledgers) {
    return (
      <div style={{ padding: 20, color: C.dim, fontFamily: FONT_MONO, fontSize: 12 }}>
        Loading…
      </div>
    );
  }

  const runs = runsData.runs;

  return (
    <div style={{ padding: "12px 16px", color: C.text, fontFamily: FONT_MONO, fontSize: 12 }}>
      {/* Header */}
      <div style={{
        color: C.cyan,
        fontSize: 18,
        fontFamily: FONT_UI,
        textShadow: `0 0 6px ${C.cyan}`,
        borderBottom: `2px solid ${C.purple}`,
        display: "inline-block",
        paddingRight: 24,
        paddingBottom: 4,
        marginBottom: 18,
      }}>
        Pipeline: raw → entities → registry
      </div>

      {/* Stage cards */}
      <div style={{ display: "flex", alignItems: "stretch", gap: 10, marginBottom: 22 }}>
        {STAGES.map((s) => (
          <div
            key={s.name}
            style={{
              flex: 1,
              border: `1px solid ${s.border}`,
              background: "rgba(0,0,0,0.45)",
              padding: "13px 14px",
            }}
          >
            <div style={{
              color: s.color,
              fontSize: 13,
              letterSpacing: 1,
              marginBottom: 6,
              textShadow: `0 0 6px ${s.color}`,
            }}>
              {s.name}
            </div>
            <div style={{ color: C.dim, fontSize: 11, marginBottom: 8 }}>{s.script}</div>
            <div style={{ color: C.text, fontSize: 12.5, lineHeight: 1.7, whiteSpace: "pre-line" }}>{s.stat}</div>
            {s.llm && (
              <div style={{ color: s.llmColor, fontSize: 10, marginTop: 8, letterSpacing: 1 }}>{s.llm}</div>
            )}
          </div>
        ))}
      </div>

      {/* Cadence runs table + Ledgers */}
      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 20 }}>
        {/* Cadence runs */}
        <div>
          <div style={{
            color: C.purple,
            fontSize: 12,
            letterSpacing: 1,
            marginBottom: 9,
            textShadow: `0 0 6px ${C.purple}`,
          }}>
            Cadence runs &nbsp;<span style={{ color: "var(--ns-dim2)" }}>discovery/_runs.jsonl</span>
          </div>
          <div style={{ border: "1px solid rgba(0,255,0,0.22)" }}>
            {/* Table header */}
            <div style={{
              display: "flex",
              gap: 10,
              padding: "7px 13px",
              background: "rgba(128,0,255,0.18)",
              borderBottom: `1px solid ${C.purple}`,
              fontSize: 11,
              color: C.text,
              letterSpacing: 1,
            }}>
              <span style={{ width: 92 }}>CADENCE</span>
              <span style={{ width: 124 }}>TIMESTAMP</span>
              <span style={{ width: 40 }}>RC</span>
              <span style={{ flex: 1 }}>DURATION</span>
            </div>
            {/* Table rows */}
            {runs.length === 0 ? (
              <div style={{ padding: "8px 13px", color: C.dim }}>No runs recorded</div>
            ) : (
              runs.map((r: Run, i: number) => (
                <div
                  key={`${r.cadence}-${r.ts}-${i}`}
                  style={{
                    display: "flex",
                    gap: 10,
                    padding: "7px 13px",
                    borderBottom: "1px dashed rgba(0,255,0,0.13)",
                    fontSize: 12,
                  }}
                >
                  <span style={{ width: 92, color: C.text }}>{r.cadence}</span>
                  <span style={{ width: 124, color: C.dim }}>{r.ts}</span>
                  <span style={{ width: 40, color: rcColor(r.rc) }}>{r.rc}</span>
                  <span style={{ flex: 1, color: "var(--ns-dim2)" }}>{fmtDuration(r.duration_s)}</span>
                </div>
              ))
            )}
          </div>
          {/* Action buttons */}
          <div style={{ marginTop: 14, display: "flex", gap: 9, flexWrap: "wrap" }}>
            <button
              onClick={handleRunWeekly}
              disabled={running}
              style={{
                cursor: running ? "not-allowed" : "pointer",
                fontSize: 12,
                padding: "7px 15px",
                borderRadius: 8,
                border: `1px solid ${C.green}`,
                color: C.green,
                background: "transparent",
                opacity: running ? 0.45 : 1,
                fontFamily: FONT_MONO,
              }}
            >
              Run weekly
            </button>
            <button
              onClick={handleDryRun}
              disabled={running}
              style={{
                cursor: running ? "not-allowed" : "pointer",
                fontSize: 12,
                padding: "7px 15px",
                borderRadius: 8,
                border: "1px solid rgba(0,243,255,0.5)",
                color: C.cyan,
                background: "transparent",
                opacity: running ? 0.45 : 1,
                fontFamily: FONT_MONO,
              }}
            >
              Dry run
            </button>
            {running && (
              <button
                onClick={handleCancel}
                style={{
                  cursor: "pointer",
                  fontSize: 12,
                  padding: "7px 15px",
                  borderRadius: 8,
                  border: "1px solid rgba(255,95,86,0.6)",
                  color: C.red,
                  background: "transparent",
                  fontFamily: FONT_MONO,
                }}
              >
                Cancel
              </button>
            )}
          </div>

          {/* Error banner — job-start failures and failed stages */}
          {errorMsg && (
            <div style={{
              marginTop: 14,
              border: "1px solid rgba(255,95,86,0.5)",
              background: "rgba(20,0,0,0.4)",
              padding: "9px 13px",
              color: C.red,
              fontSize: 12,
            }}>
              error — {errorMsg}
            </div>
          )}

          {/* Job card — shown once a run has been started */}
          {jobLabel && (
            <div style={{
              marginTop: 14,
              border: "1px solid rgba(128,0,255,0.4)",
              background: "rgba(8,0,14,0.5)",
              padding: "11px 13px",
            }}>
              <div style={{ color: C.purple, fontSize: 11, letterSpacing: 1, marginBottom: 6 }}>
                JOB {jobLabel} &nbsp;
                <span style={{ color: jobState === "done" ? C.greenSoft : jobState === "error" ? C.red : C.cyan }}>
                  {jobState ?? "starting"}
                  {jobRc != null ? ` · rc=${jobRc}` : ""}
                </span>
              </div>
              <div style={{
                maxHeight: 120,
                overflowY: "auto",
                fontSize: 11,
                lineHeight: 1.6,
                color: C.dim,
              }}>
                {lines.length === 0
                  ? <span style={{ color: "#444" }}>waiting for output…</span>
                  : lines.map((l, i) => <div key={i}>{l}</div>)
                }
              </div>
            </div>
          )}
        </div>

        {/* Discovery ledgers */}
        <div>
          <div style={{
            color: C.purple,
            fontSize: 12,
            letterSpacing: 1,
            marginBottom: 9,
            textShadow: `0 0 6px ${C.purple}`,
          }}>
            Discovery ledgers
          </div>
          <div style={{
            border: "1px solid rgba(0,255,0,0.22)",
            background: "rgba(0,0,0,0.4)",
            padding: "13px 15px",
            fontSize: 12,
            lineHeight: 1.9,
            marginBottom: 16,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: C.text }}>proposals.jsonl</span>
              <span style={{ color: C.dim }}>append-only · {ledgers.proposals} rec</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: C.text }}>promoted.jsonl</span>
              <span style={{ color: C.dim }}>append-only · {ledgers.promoted} rec</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: C.text }}>_manifest.jsonl</span>
              <span style={{ color: C.dim }}>{ledgers.manifest} hashed</span>
            </div>
          </div>
          {/* Rule card */}
          <div style={{
            border: "1px solid rgba(128,0,255,0.4)",
            borderLeft: `2px solid ${C.purple}`,
            background: "rgba(8,0,14,0.4)",
            padding: "12px 15px",
            fontSize: 11.5,
            lineHeight: 1.7,
            color: C.text,
          }}>
            <div style={{ color: C.purple, letterSpacing: 1, marginBottom: 6 }}>THE RULE THAT NEVER BENDS</div>
            The LLM <span style={{ color: C.cyan }}>proposes</span>; deterministic code{" "}
            <span style={{ color: C.greenSoft }}>commits</span>. No non-deterministic write to the vocabulary, ever.
          </div>
        </div>
      </div>
    </div>
  );
}
