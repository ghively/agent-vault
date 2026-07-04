import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useEntities, useEntity } from "../api/hooks";
import { recompileEntity } from "../api/jobs";
import { C, FONT_MONO, FONT_UI } from "../theme";
import type { EntityRow, FactRow, LinkRow } from "../api/types";

// ── helpers ──────────────────────────────────────────────────────────────────

function statusBg(status: string): string {
  if (status === "compiled") return "rgba(0,255,0,0.12)";
  if (status === "needs_review") return "rgba(255,189,46,0.15)";
  return "rgba(136,136,136,0.15)";
}

function statusColor(status: string): string {
  if (status === "compiled") return C.greenSoft;
  if (status === "needs_review") return C.amber;
  return C.dim;
}

function statusLabel(status: string): string {
  if (status === "compiled") return "compiled";
  if (status === "needs_review") return "needs review";
  return status;
}

// ── sub-components ────────────────────────────────────────────────────────────

interface GroupState { type: string; items: EntityRow[]; open: boolean }

function IndexPane({
  wikiQuery,
  setWikiQuery,
  detailSlug,
  setDetailSlug,
}: {
  wikiQuery: string;
  setWikiQuery: (q: string) => void;
  detailSlug: string;
  setDetailSlug: (s: string) => void;
}) {
  const { data } = useEntities();
  const rows: EntityRow[] = data?.rows ?? [];
  const total = data?.total ?? 0;

  // Filter by local wikiQuery
  const filtered = wikiQuery
    ? rows.filter(
        (r) =>
          r.slug.toLowerCase().includes(wikiQuery.toLowerCase()) ||
          r.type.toLowerCase().includes(wikiQuery.toLowerCase()),
      )
    : rows;

  // Group by type, preserving insertion order
  const groupMap = new Map<string, EntityRow[]>();
  for (const row of filtered) {
    const bucket = groupMap.get(row.type) ?? [];
    bucket.push(row);
    groupMap.set(row.type, bucket);
  }

  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  const toggleGroup = (type: string) =>
    setOpenGroups((prev) => ({ ...prev, [type]: !(prev[type] ?? true) }));

  const isOpen = (type: string) => openGroups[type] ?? true;

  return (
    <div
      style={{
        flex: "none",
        width: 206,
        overflow: "auto",
        borderRight: "1px solid rgba(0,255,0,0.18)",
        paddingRight: 12,
      }}
    >
      {/* header */}
      <div
        style={{
          color: C.purple,
          fontSize: 11,
          letterSpacing: 1,
          marginBottom: 8,
          textShadow: `0 0 6px ${C.purple}`,
        }}
      >
        ~/entities · {total}
      </div>

      {/* search */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          border: "1px solid rgba(0,255,0,0.3)",
          background: "rgba(0,0,0,0.4)",
          padding: "5px 9px",
          marginBottom: 10,
        }}
      >
        <span style={{ color: C.purple, fontSize: 12 }}>⌕</span>
        <input
          value={wikiQuery}
          onChange={(e) => setWikiQuery(e.target.value)}
          placeholder="filter…"
          style={{
            flex: 1,
            minWidth: 0,
            background: "transparent",
            border: "none",
            outline: "none",
            color: C.text,
            fontFamily: FONT_UI,
            fontSize: 12,
          }}
        />
      </div>

      {/* groups */}
      {Array.from(groupMap.entries()).map(([type, items]) => (
        <div key={type}>
          {/* group header */}
          <div
            onClick={() => toggleGroup(type)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              color: "#7faf8f",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 1,
              margin: "10px 0 3px",
              textTransform: "uppercase",
              cursor: "pointer",
              padding: "4px 6px",
              borderRadius: 6,
            }}
          >
            <span style={{ fontSize: 10, width: 10 }}>
              {isOpen(type) ? "▾" : "▸"}
            </span>
            {type}
            <span style={{ color: "#5f7f5f", fontWeight: 400 }}>
              · {items.length}
            </span>
          </div>

          {/* group items */}
          {isOpen(type) &&
            items.map((item) => {
              const isActive = item.slug === detailSlug;
              return (
                <div
                  key={item.slug}
                  onClick={() => setDetailSlug(item.slug)}
                  style={{
                    padding: "8px 11px",
                    borderRadius: 7,
                    borderLeft: `2px solid ${isActive ? C.cyan : "rgba(0,255,0,0.25)"}`,
                    background: isActive
                      ? "rgba(0,243,255,0.07)"
                      : "transparent",
                    cursor: "pointer",
                    marginBottom: 1,
                  }}
                >
                  <div
                    style={{
                      color: isActive ? C.cyan : C.text,
                      fontSize: 13.5,
                      fontWeight: 500,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {item.slug}
                  </div>
                  <div style={{ color: "#6a9a6a", fontSize: 11 }}>
                    {item.type}/{item.subtype}
                  </div>
                </div>
              );
            })}
        </div>
      ))}

      {filtered.length === 0 && (
        <div style={{ color: C.dim, fontSize: 12, padding: "8px 6px" }}>
          no matches
        </div>
      )}
    </div>
  );
}

function FactsTable({ facts }: { facts: FactRow[] }) {
  return (
    <div>
      {facts.map((f, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: 12,
            padding: "5px 0",
            fontSize: 13,
          }}
        >
          <span style={{ color: "var(--ns-muted-green)", width: 98, flex: "none" }}>{f.k}</span>
          <span style={{ color: C.text }}>{f.v}</span>
        </div>
      ))}
    </div>
  );
}

function LinksSection({
  links,
  setDetailSlug,
}: {
  links: LinkRow[];
  setDetailSlug: (s: string) => void;
}) {
  if (links.length === 0) {
    return (
      <div style={{ color: "#5f7f5f", fontSize: 12.5, padding: "7px 0" }}>
        No related entities yet.
      </div>
    );
  }
  return (
    <>
      {links.map((l, i) => {
        const canNav = l.exists;
        return (
          <div
            key={i}
            onClick={
              canNav
                ? () => setDetailSlug(l.ref.split("/").pop() ?? l.ref)
                : undefined
            }
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 12,
              padding: "7px 0",
              cursor: canNav ? "pointer" : "default",
            }}
          >
            <span
              style={{
                color: "var(--ns-muted-green)",
                fontSize: 11.5,
                minWidth: 86,
                flex: "none",
              }}
            >
              {l.label}
            </span>
            <span
              style={{
                color: canNav ? C.cyan : C.dim,
                fontSize: 13,
                fontWeight: 500,
              }}
            >
              {l.title}
              {canNav ? " →" : ""}
            </span>
          </div>
        );
      })}
    </>
  );
}

const CARD_STYLE: React.CSSProperties = {
  borderRadius: 11,
  border: "1px solid rgba(0,255,0,0.18)",
  background: "rgba(0,0,0,0.35)",
  padding: "14px 16px",
  marginBottom: 22,
};

const SEC_STYLE: React.CSSProperties = {
  color: C.cyan,
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: 1,
  textTransform: "uppercase",
};

const TAG_STYLE: React.CSSProperties = {
  fontSize: 10,
  color: C.dim,
  border: "1px solid rgba(136,136,136,0.3)",
  borderRadius: 4,
  padding: "2px 7px",
};

// ── main component ────────────────────────────────────────────────────────────

export function Wiki() {
  const [detailSlug, setDetailSlug] = useState("");
  const [wikiQuery, setWikiQuery] = useState("");
  const [recompiling, setRecompiling] = useState(false);
  const [recompileSummary, setRecompileSummary] = useState<Record<string, unknown> | null>(null);
  const [recompileError, setRecompileError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: detail, isLoading } = useEntity(detailSlug);

  // POST /api/entities/{slug}/recompile is SYNCHRONOUS: the response IS the
  // compile summary, so completion of the fetch is completion of the work.
  async function handleRecompile() {
    if (!detailSlug || recompiling) return;
    if (!window.confirm("Recompile this entity with the LLM?")) return;
    setRecompiling(true);
    setRecompileSummary(null);
    setRecompileError(null);
    try {
      const summary = await recompileEntity(detailSlug);
      setRecompileSummary(summary);
      queryClient.invalidateQueries({ queryKey: ["entity", detailSlug] });
      queryClient.invalidateQueries({ queryKey: ["entities"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    } catch (e) {
      setRecompileError(e instanceof Error ? e.message : "recompile failed");
    } finally {
      setRecompiling(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        height: "100%",
        padding: "12px 16px",
        color: C.text,
        fontFamily: FONT_MONO,
        fontSize: 12,
        boxSizing: "border-box",
      }}
    >
      {/* Left: entity index */}
      <IndexPane
        wikiQuery={wikiQuery}
        setWikiQuery={setWikiQuery}
        detailSlug={detailSlug}
        setDetailSlug={setDetailSlug}
      />

      {/* Right: entity page */}
      <div style={{ flex: 1, overflow: "auto", padding: "2px 8px 12px 0" }}>
        {isLoading || !detail ? (
          <div style={{ color: C.dim, padding: 20 }}>
            {detailSlug ? "Loading…" : "Select an entity from the index."}
          </div>
        ) : (
          <>
            {/* title + status */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
                marginBottom: 5,
              }}
            >
              <div
                style={{
                  color: C.text,
                  fontSize: 23,
                  fontWeight: 600,
                  letterSpacing: -0.3,
                }}
              >
                {detail.title}
              </div>
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 600,
                  padding: "3px 11px",
                  borderRadius: 20,
                  background: statusBg(detail.status),
                  color: statusColor(detail.status),
                }}
              >
                {statusLabel(detail.status)}
              </span>
            </div>

            {/* subtitle row */}
            <div
              style={{
                color: "var(--ns-muted-green)",
                fontSize: 12.5,
                borderBottom: "1px solid rgba(255,255,255,0.08)",
                paddingBottom: 14,
                marginBottom: 18,
              }}
            >
              {detail.type}/{detail.subtype} · conf {String(detail.confidence)}
            </div>

            {/* two-column grid */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1.1fr",
                gap: 22,
              }}
            >
              {/* left column: facts + links */}
              <div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <span style={SEC_STYLE}>Details</span>
                  <span style={TAG_STYLE}>recorded by ingest</span>
                </div>
                <div style={CARD_STYLE}>
                  <FactsTable facts={detail.facts} />
                </div>

                <div style={{ marginBottom: 10 }}>
                  <span style={SEC_STYLE}>Linked entities</span>
                </div>
                <div style={{ ...CARD_STYLE, padding: "6px 16px" }}>
                  <LinksSection
                    links={detail.links}
                    setDetailSlug={setDetailSlug}
                  />
                </div>
              </div>

              {/* right column: prose + sources + buttons */}
              <div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <span style={SEC_STYLE}>Summary</span>
                  <span style={TAG_STYLE}>written by AI</span>
                </div>

                {/* prose card */}
                <div
                  style={{
                    borderRadius: 11,
                    border: "1px solid rgba(0,243,255,0.22)",
                    borderLeft: "3px solid #00d9ff",
                    background: "rgba(0,18,24,0.4)",
                    padding: "16px 18px",
                    fontSize: 14,
                    lineHeight: 1.7,
                    color: C.text,
                    marginBottom: 22,
                  }}
                >
                  {detail.prose}
                  {detail.prose.includes("[NEEDS SOURCE]") && (
                    <div
                      style={{
                        marginTop: 13,
                        fontSize: 12,
                        color: "#ffc04d",
                        background: "rgba(255,176,46,0.08)",
                        border: "1px solid rgba(255,176,46,0.25)",
                        borderRadius: 8,
                        padding: "9px 12px",
                      }}
                    >
                      [NEEDS SOURCE] — citation required for one or more claims.
                    </div>
                  )}
                </div>

                {/* source files */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <span style={SEC_STYLE}>Source files</span>
                  <span style={TAG_STYLE}>
                    {detail.sources.length} file
                    {detail.sources.length !== 1 ? "s" : ""}
                  </span>
                </div>
                <div
                  style={{ ...CARD_STYLE, padding: "6px 16px", marginBottom: 20 }}
                >
                  {detail.sources.map((src, i) => {
                    const parts = src.replace(/\\/g, "/").split("/");
                    const name = parts[parts.length - 1] ?? src;
                    const dir = parts.slice(0, -1).join("/");
                    return (
                      <div
                        key={i}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          gap: 8,
                          padding: "8px 0",
                          borderRadius: 6,
                        }}
                      >
                        <span
                          style={{
                            color: "#6fd4ff",
                            fontSize: 13,
                            fontWeight: 500,
                          }}
                        >
                          {name}
                        </span>
                        <span style={{ color: "#5f7f5f", fontSize: 11 }}>
                          {dir}
                        </span>
                      </div>
                    );
                  })}
                  {detail.sources.length === 0 && (
                    <div style={{ color: C.dim, fontSize: 12, padding: "7px 0" }}>
                      No source files.
                    </div>
                  )}
                </div>

                {/* action buttons */}
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <button
                    onClick={handleRecompile}
                    disabled={recompiling}
                    style={{
                      cursor: recompiling ? "wait" : "pointer",
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: `1px solid ${C.cyan}`,
                      background: "rgba(0,243,255,0.08)",
                      color: C.cyan,
                      fontFamily: FONT_MONO,
                      opacity: recompiling ? 0.55 : 1,
                    }}
                  >
                    {recompiling ? "Recompiling…" : "Recompile"}
                  </button>
                  <button
                    disabled
                    title="not implemented yet"
                    style={{
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: "1px solid rgba(0,255,0,0.3)",
                      background: "transparent",
                      color: C.text,
                      cursor: "not-allowed",
                      fontFamily: FONT_MONO,
                      opacity: 0.45,
                    }}
                  >
                    Edit details
                  </button>
                  <button
                    disabled
                    title="not implemented yet"
                    style={{
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: "1px solid rgba(0,255,0,0.3)",
                      background: "transparent",
                      color: C.text,
                      cursor: "not-allowed",
                      fontFamily: FONT_MONO,
                      opacity: 0.45,
                    }}
                  >
                    Open in editor
                  </button>
                </div>

                {/* recompile result panel */}
                {recompileError && (
                  <div
                    style={{
                      marginTop: 16,
                      borderRadius: 9,
                      border: "1px solid rgba(255,95,86,0.4)",
                      background: "rgba(24,0,0,0.45)",
                      padding: "12px 14px",
                      color: C.red,
                      fontSize: 12,
                    }}
                  >
                    recompile failed — {recompileError}
                  </div>
                )}
                {recompileSummary && (
                  <div
                    style={{
                      marginTop: 16,
                      borderRadius: 9,
                      border: "1px solid rgba(0,243,255,0.2)",
                      background: "rgba(0,18,24,0.55)",
                      padding: "12px 14px",
                    }}
                  >
                    <div
                      style={{
                        color: C.greenSoft,
                        fontSize: 11,
                        fontWeight: 700,
                        letterSpacing: 1,
                        textTransform: "uppercase",
                        marginBottom: 8,
                      }}
                    >
                      Recompile complete
                    </div>
                    <pre
                      style={{
                        fontFamily: FONT_MONO,
                        fontSize: 11.5,
                        color: C.dim,
                        maxHeight: 160,
                        overflow: "auto",
                        margin: 0,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {JSON.stringify(recompileSummary, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
