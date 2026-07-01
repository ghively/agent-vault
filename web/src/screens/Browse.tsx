import React, { useState } from "react";
import { useEntities } from "../api/hooks";
import { C, FONT_MONO, FONT_UI } from "../theme";
import type { EntityRow } from "../api/types";

const TYPE_CHIPS = ["all", "asset", "account", "policy", "contact", "document", "appliance", "vehicle", "property"];

function statusColor(status: string): string {
  if (status === "compiled") return C.greenSoft;
  if (status === "needs_review") return C.amber;
  return C.dim;
}

export function Browse() {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");

  const { data, isLoading, isError } = useEntities(query, typeFilter === "all" ? "" : typeFilter);
  const rows: EntityRow[] = data?.rows ?? [];
  const total: number = data?.total ?? 0;
  const resultCount = `${rows.length} / ${total}`;

  return (
    <div style={{ padding: "12px 16px", color: C.text, fontFamily: FONT_MONO, fontSize: 12 }}>
      {/* Header */}
      <div style={{
        color: C.cyan,
        fontSize: 18,
        textShadow: `0 0 6px ${C.cyan}`,
        borderBottom: `2px solid ${C.purple}`,
        display: "inline-block",
        paddingRight: 24,
        paddingBottom: 4,
        marginBottom: 16,
      }}>
        Directory Listing: ~/entities/
      </div>

      {/* Search bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        border: `1px solid ${C.green}`,
        background: "rgba(0,0,0,0.55)",
        padding: "7px 13px",
        marginBottom: 14,
        boxShadow: "0 0 14px rgba(0,255,0,0.12)",
      }}>
        <span style={{ color: C.purple }}>$</span>
        <span style={{ color: C.text, fontSize: 13 }}>synapse find</span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="type to filter…"
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            color: C.cyan,
            fontFamily: FONT_UI,
            fontSize: 13,
          }}
        />
        <span style={{ color: C.dim, fontSize: 11 }}>{resultCount}</span>
      </div>

      {/* Type chips */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 14 }}>
        {TYPE_CHIPS.map((chip) => {
          const active = typeFilter === chip;
          return (
            <span
              key={chip}
              role="button"
              tabIndex={0}
              aria-pressed={active}
              onClick={() => setTypeFilter(chip)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTypeFilter(chip); }
              }}
              style={{
                cursor: "pointer",
                fontSize: 11,
                padding: "4px 11px",
                borderRadius: 6,
                border: `1px solid ${active ? C.purple : "rgba(0,255,0,0.3)"}`,
                color: active ? C.purple : C.dim,
                background: active ? "rgba(128,0,255,0.18)" : "transparent",
                boxShadow: active ? `0 0 6px ${C.purple}` : "none",
              }}
            >
              {chip}
            </span>
          );
        })}
      </div>

      {/* Results table */}
      <div style={{ border: "1px solid rgba(0,255,0,0.25)" }}>
        {/* Column headers */}
        <div style={{
          display: "flex", gap: 12, padding: "8px 14px",
          background: "rgba(128,0,255,0.18)",
          borderBottom: `1px solid ${C.purple}`,
          fontSize: 11, letterSpacing: 1, color: C.text,
        }}>
          <span style={{ width: 190 }}>SLUG</span>
          <span style={{ width: 165 }}>TYPE / SUBTYPE</span>
          <span style={{ width: 92 }}>STATUS</span>
          <span style={{ width: 46 }}>CONF</span>
          <span style={{ flex: 1 }}>TAGS</span>
        </div>

        {/* Error / loading states */}
        {isError && (
          <div style={{ padding: "18px 14px", color: C.red, fontSize: 12 }}>
            error — could not reach /api/entities
          </div>
        )}
        {!isError && isLoading && (
          <div style={{ padding: "18px 14px", color: C.dim, fontSize: 12 }}>loading…</div>
        )}

        {/* Rows */}
        {!isError && !isLoading && rows.map((row) => (
          <div
            key={row.slug}
            style={{
              display: "flex", gap: 12, padding: "7px 14px",
              borderBottom: "1px dashed rgba(0,255,0,0.13)",
              fontSize: 12, alignItems: "center",
            }}
          >
            <span style={{ width: 190, color: C.greenSoft }}>{row.slug}</span>
            <span style={{ width: 165, color: C.dim }}>{row.type}{row.subtype ? `/${row.subtype}` : ""}</span>
            <span style={{ width: 92, color: statusColor(row.status) }}>{row.status}</span>
            <span style={{ width: 46, color: C.text }}>{String(row.confidence)}</span>
            <span style={{ flex: 1, color: C.purple, fontSize: 11 }}>{Array.isArray(row.tags) ? row.tags.join(", ") : row.tags}</span>
          </div>
        ))}

        {/* Empty state */}
        {!isError && !isLoading && rows.length === 0 && (
          <div style={{ padding: "18px 14px", color: C.dim, fontSize: 12 }}>
            no matches — try another query or clear the filter
          </div>
        )}
      </div>
    </div>
  );
}
