import React, { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useEntities, useEntity, useEntityRaw, useSchema } from "../api/hooks";
import { usePatchEntity, useSaveEntityRaw } from "../api/mutations";
import { recompileEntity } from "../api/jobs";
import { C, FONT_MONO, FONT_UI } from "../theme";
import type { EntityDetail, EntityPatchBody, EntityRow, FactRow, LinkRow } from "../api/types";

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
  const [editOpen, setEditOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: detail, isLoading } = useEntity(detailSlug);

  // Selecting another entity abandons any open editor for the previous one.
  useEffect(() => {
    setEditOpen(false);
    setRawOpen(false);
    setSavedNotice(null);
  }, [detailSlug]);

  // The "saved" indicator is deliberately brief.
  useEffect(() => {
    if (!savedNotice) return;
    const t = setTimeout(() => setSavedNotice(null), 4000);
    return () => clearTimeout(t);
  }, [savedNotice]);

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
        {rawOpen && detailSlug ? (
          <RawEditorPane
            slug={detailSlug}
            onExit={(savedPath) => {
              setRawOpen(false);
              if (savedPath) setSavedNotice(`saved ${savedPath}`);
            }}
          />
        ) : isLoading || !detail ? (
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
                    onClick={() => setEditOpen(true)}
                    style={{
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: "1px solid rgba(0,255,0,0.3)",
                      background: "transparent",
                      color: C.text,
                      cursor: "pointer",
                      fontFamily: FONT_MONO,
                    }}
                  >
                    Edit details
                  </button>
                  <button
                    onClick={() => setRawOpen(true)}
                    style={{
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 7,
                      border: "1px solid rgba(0,255,0,0.3)",
                      background: "transparent",
                      color: C.text,
                      cursor: "pointer",
                      fontFamily: FONT_MONO,
                    }}
                  >
                    Open in editor
                  </button>
                </div>

                {savedNotice && (
                  <div style={{ marginTop: 12, color: C.greenSoft, fontSize: 12 }}>
                    ✓ {savedNotice}
                  </div>
                )}

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

      {editOpen && detail && (
        <EditDetailsModal detail={detail} onClose={() => setEditOpen(false)} />
      )}
    </div>
  );
}

// ── Edit details modal ────────────────────────────────────────────────────────

const EDIT_LABEL_STYLE: React.CSSProperties = {
  display: "block",
  color: C.dim,
  fontSize: 11,
  letterSpacing: 1,
  marginBottom: 4,
  textTransform: "uppercase",
};

const EDIT_INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "rgba(0,0,0,0.4)",
  border: "1px solid rgba(0,255,0,0.3)",
  borderRadius: 5,
  color: C.text,
  fontFamily: FONT_MONO,
  fontSize: 12,
  padding: "6px 9px",
  outline: "none",
};

const tagsEqual = (a: string[], b: string[]) =>
  a.length === b.length && a.every((t, i) => t === b[i]);

function EditDetailsModal({ detail, onClose }: { detail: EntityDetail; onClose: () => void }) {
  const { data: schema } = useSchema();
  const { data: entitiesData } = useEntities();
  const patch = usePatchEntity();
  const titleRef = useRef<HTMLInputElement>(null);

  // The detail response does not reliably carry tags/notes today: fall back to
  // the entities-list row for tags (EntityRow has them) and to a "notes" fact.
  const rowTags = entitiesData?.rows.find((r) => r.slug === detail.slug)?.tags;
  const [initial] = useState(() => ({
    title: detail.title,
    tags: detail.tags ?? rowTags ?? [],
    notes: detail.notes ?? detail.facts.find((f) => f.k === "notes")?.v ?? "",
  }));

  const [title, setTitle] = useState(initial.title);
  const [tags, setTags] = useState<string[]>(initial.tags);
  const [notes, setNotes] = useState(initial.notes);

  // Escape closes; initial focus on the title input (Creds modal conventions).
  useEffect(() => {
    titleRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const availableTags = (schema?.tags ?? []).filter((t) => !tags.includes(t));

  // Only send the keys the user actually changed. A blank notes field means
  // "leave unchanged" — the field may simply not have been prefillable.
  const body: EntityPatchBody = {};
  if (title !== initial.title) body.title = title;
  if (!tagsEqual(tags, initial.tags)) body.tags = tags;
  if (notes !== initial.notes && notes.trim() !== "") body.notes = notes;
  const changed = Object.keys(body).length > 0;

  const save = () => {
    if (patch.isPending) return;
    if (!changed) {
      onClose();
      return;
    }
    patch.mutate({ slug: detail.slug, body }, { onSuccess: () => onClose() });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Edit details for ${detail.slug}`}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.75)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div style={{
        background: "#0a0a0a",
        border: `1px solid ${C.cyan}`,
        borderRadius: 8,
        padding: "24px 28px",
        minWidth: 380,
        maxWidth: 520,
        fontFamily: FONT_MONO,
        color: C.text,
        fontSize: 13,
      }}>
        <div style={{ color: C.cyan, fontSize: 13, letterSpacing: 1, marginBottom: 16 }}>
          EDIT DETAILS · {detail.slug}
        </div>

        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={EDIT_LABEL_STYLE}>title</span>
          <input
            ref={titleRef}
            aria-label="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={EDIT_INPUT_STYLE}
          />
        </label>

        <div style={{ marginBottom: 12 }}>
          <span style={EDIT_LABEL_STYLE}>tags</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
            {tags.map((t) => (
              <span
                key={t}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  fontSize: 11,
                  color: C.greenSoft,
                  border: "1px solid rgba(0,255,0,0.3)",
                  borderRadius: 4,
                  padding: "2px 7px",
                }}
              >
                {t}
                <button
                  onClick={() => setTags(tags.filter((x) => x !== t))}
                  aria-label={`remove tag ${t}`}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: C.dim,
                    cursor: "pointer",
                    fontFamily: FONT_MONO,
                    fontSize: 11,
                    padding: 0,
                  }}
                >
                  ✕
                </button>
              </span>
            ))}
            {tags.length === 0 && (
              <span style={{ color: C.dim, fontSize: 11 }}>no tags</span>
            )}
          </div>
          {/* Free text is rejected server-side (unknown tags → 400), so tags
              can only be added from the registry vocabulary. */}
          <select
            aria-label="add tag"
            value=""
            onChange={(e) => {
              if (e.target.value) setTags([...tags, e.target.value]);
            }}
            disabled={availableTags.length === 0}
            style={{ ...EDIT_INPUT_STYLE, opacity: availableTags.length === 0 ? 0.5 : 1 }}
          >
            <option value="">add tag…</option>
            {availableTags.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <label style={{ display: "block", marginBottom: 16 }}>
          <span style={EDIT_LABEL_STYLE}>notes</span>
          <textarea
            aria-label="notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="optional — blank leaves notes unchanged"
            style={{ ...EDIT_INPUT_STYLE, resize: "vertical" }}
          />
        </label>

        {patch.error != null && (
          <div style={{ color: C.red, fontSize: 11.5, marginBottom: 12, whiteSpace: "pre-wrap" }}>
            {patch.error instanceof Error ? patch.error.message : "request failed"}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            onClick={onClose}
            style={{
              padding: "6px 16px",
              borderRadius: 5,
              border: `1px solid ${C.dim}`,
              background: "transparent",
              color: C.dim,
              fontFamily: FONT_MONO,
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={patch.isPending}
            style={{
              padding: "6px 16px",
              borderRadius: 5,
              border: `1px solid ${C.cyan}`,
              background: "rgba(0,243,255,0.08)",
              color: C.cyan,
              fontFamily: FONT_MONO,
              fontSize: 12,
              cursor: patch.isPending ? "wait" : "pointer",
              opacity: patch.isPending ? 0.55 : 1,
            }}
          >
            {patch.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Raw editor pane ───────────────────────────────────────────────────────────

function RawEditorPane({
  slug,
  onExit,
}: {
  slug: string;
  /** savedPath is non-null when the exit follows a successful save. */
  onExit: (savedPath: string | null) => void;
}) {
  const { data, isLoading, isError } = useEntityRaw(slug, true);
  const save = useSaveEntityRaw();
  const [content, setContent] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Seed the textarea once the file arrives.
  useEffect(() => {
    if (data && content === null) {
      setContent(data.content);
    }
  }, [data, content]);

  useEffect(() => {
    if (data) taRef.current?.focus();
  }, [data]);

  const dirty = data != null && content !== null && content !== data.content;

  const requestExit = useCallback(() => {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onExit(null);
  }, [dirty, onExit]);

  // Escape exits (asking first when dirty).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") requestExit();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [requestExit]);

  const doSave = () => {
    if (content === null || save.isPending) return;
    save.mutate({ slug, content }, { onSuccess: (res) => onExit(res.path) });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={SEC_STYLE}>Raw editor</span>
        <span style={{ color: "#6fd4ff", fontSize: 12 }}>{data?.path ?? slug}</span>
        {dirty && (
          <span style={{ color: C.amber, fontSize: 11 }}>● unsaved changes</span>
        )}
      </div>
      <div style={{ color: C.dim, fontSize: 11, marginBottom: 10 }}>
        editing the entity file directly — slug/type edits are rejected; use Reclassify to move an entity
      </div>

      {isError && (
        <div style={{ color: C.red, fontSize: 12 }}>
          error — could not load the raw entity file
        </div>
      )}
      {!isError && isLoading && (
        <div style={{ color: C.dim, fontSize: 12 }}>loading file…</div>
      )}

      {!isError && data && (
        <>
          <textarea
            ref={taRef}
            aria-label={`raw content of ${slug}`}
            value={content ?? data.content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
            style={{
              flex: 1,
              minHeight: 260,
              boxSizing: "border-box",
              width: "100%",
              background: "rgba(0,0,0,0.45)",
              border: "1px solid rgba(0,255,0,0.3)",
              borderRadius: 7,
              color: C.text,
              fontFamily: FONT_MONO,
              fontSize: 12,
              lineHeight: 1.6,
              padding: "10px 12px",
              outline: "none",
              resize: "none",
            }}
          />

          {save.error != null && (
            <div style={{ color: C.red, fontSize: 11.5, marginTop: 10, whiteSpace: "pre-wrap" }}>
              {save.error instanceof Error ? save.error.message : "request failed"}
            </div>
          )}

          <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <button
              onClick={doSave}
              disabled={!dirty || save.isPending}
              style={{
                fontSize: 12,
                padding: "6px 14px",
                borderRadius: 7,
                border: `1px solid ${C.cyan}`,
                background: "rgba(0,243,255,0.08)",
                color: C.cyan,
                fontFamily: FONT_MONO,
                cursor: save.isPending ? "wait" : dirty ? "pointer" : "not-allowed",
                opacity: !dirty || save.isPending ? 0.55 : 1,
              }}
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            <button
              onClick={requestExit}
              style={{
                fontSize: 12,
                padding: "6px 14px",
                borderRadius: 7,
                border: `1px solid ${C.dim}`,
                background: "transparent",
                color: C.dim,
                fontFamily: FONT_MONO,
                cursor: "pointer",
              }}
            >
              Discard
            </button>
          </div>
        </>
      )}
    </div>
  );
}
