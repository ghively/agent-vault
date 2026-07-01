import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRuns, useLedgers } from "../api/hooks";
import { startJob, useJobStream } from "../api/jobs";
import { C, FONT_MONO, FONT_UI } from "../theme";
import type { Run } from "../api/types";

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
  const [jobId, setJobId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const stream = useJobStream(jobId);

  // Invalidate runs + ledgers when the job finishes
  React.useEffect(() => {
    if (stream.state === "done") {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["ledgers"] });
      setRunning(false);
    }
    if (stream.state === "error") {
      setRunning(false);
    }
  }, [stream.state, qc]);

  async function handleRunWeekly() {
    if (!window.confirm("Start the weekly cadence run?")) return;
    setRunning(true);
    const id = await startJob("weekly");
    setJobId(id);
  }

  async function handleDryRun() {
    setRunning(true);
    const id = await startJob("lint");
    setJobId(id);
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
          </div>

          {/* Job card — shown when a job is active */}
          {jobId && (
            <div style={{
              marginTop: 14,
              border: "1px solid rgba(128,0,255,0.4)",
              background: "rgba(8,0,14,0.5)",
              padding: "11px 13px",
            }}>
              <div style={{ color: C.purple, fontSize: 11, letterSpacing: 1, marginBottom: 6 }}>
                JOB {jobId} &nbsp;
                <span style={{ color: stream.state === "done" ? C.greenSoft : stream.state === "error" ? C.red : C.cyan }}>
                  {stream.state ?? "starting"}
                  {stream.rc != null ? ` · rc=${stream.rc}` : ""}
                </span>
              </div>
              <div style={{
                maxHeight: 120,
                overflowY: "auto",
                fontSize: 11,
                lineHeight: 1.6,
                color: C.dim,
              }}>
                {stream.lines.length === 0
                  ? <span style={{ color: "#444" }}>waiting for output…</span>
                  : stream.lines.map((l, i) => <div key={i}>{l}</div>)
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
