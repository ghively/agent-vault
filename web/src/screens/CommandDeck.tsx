import React, { useState, KeyboardEvent } from "react";
import { useAsk, useStatus } from "../api/hooks";
import { C, FONT_UI, FONT_MONO } from "../theme";
import type { AskResponse } from "../api/types";

const CHIPS = [
  "what's due this week",
  "expiring soon",
  "find furnace",
  "find insurance",
  "find car",
];

function FooterTile({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ flex: 1, padding: "11px 14px", borderRight: `1px dashed rgba(0,255,0,0.2)` }}>
      <div style={{ color: C.dim, fontSize: 10, letterSpacing: 1 }}>{label}</div>
      <div style={{ color, fontSize: 12, marginTop: 3 }}>{value}</div>
    </div>
  );
}

function AnswerCard({
  data,
  isLoading,
  isError,
  activeQuery,
}: {
  data: AskResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  activeQuery: string;
}) {
  const cardStyle = {
    width: "92%", maxWidth: 620, marginTop: 24,
    border: "1px solid rgba(128,0,255,0.5)",
    borderLeft: `2px solid ${C.purple}`,
    background: "rgba(10,10,15,0.85)",
    padding: "16px 20px",
    boxShadow: "inset 0 0 50px rgba(0,0,0,0.7)",
  };

  if (activeQuery && isLoading) {
    return (
      <div style={cardStyle}>
        <div style={{ color: C.dim, fontSize: 11, letterSpacing: 1 }}>querying</div>
        <div style={{ color: C.text, fontSize: 14, lineHeight: 1.7 }}>…querying the vault</div>
      </div>
    );
  }

  if (isError) {
    return (
      <div style={cardStyle}>
        <div style={{ color: C.dim, fontSize: 11, letterSpacing: 1 }}>error</div>
        <div style={{ color: C.red, fontSize: 14, lineHeight: 1.7 }}>
          Query failed — check your connection or token.
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={cardStyle}>
        <div style={{ color: C.dim, fontSize: 11, letterSpacing: 1 }}>awaiting query</div>
        <div style={{ color: C.text, fontSize: 14, lineHeight: 1.7 }}>Type a question above and press Enter.</div>
      </div>
    );
  }

  const { intent } = data;
  const items = data?.items ?? [];

  return (
    <div style={cardStyle}>
      <div style={{ color: C.dim, fontSize: 11, letterSpacing: 1, marginBottom: 8 }}>
        intent: {intent} · {items.length} result{items.length !== 1 ? "s" : ""}
      </div>
      <div style={{ color: C.text, fontSize: 14, lineHeight: 1.7 }}>
        {items.length === 0 ? (
          <span style={{ color: C.dim }}>No results found.</span>
        ) : (
          items.map((item, i) => (
            <div key={item.slug ?? i} style={{ marginBottom: 4 }}>
              <span style={{ color: C.cyan }}>{item.title ?? item.slug}</span>
              {item.type && (
                <span style={{ color: C.dim, fontSize: 11, marginLeft: 8 }}>{item.type}</span>
              )}
              {item.date && (
                <span style={{ color: C.amber, fontSize: 12, marginLeft: 8 }}>{item.date}</span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function CommandDeck() {
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");

  const { data: askData, isError, isLoading } = useAsk(activeQuery);
  const { data: statusData } = useStatus();

  function handleKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && query.trim()) {
      setActiveQuery(query.trim());
    }
  }

  const nextDue = statusData?.due?.[0];
  const reviewCount = statusData?.counts.needs_review ?? 0;
  const total = statusData?.counts.total ?? 0;
  const compiled = statusData?.counts.compiled ?? 0;
  const lastRun = statusData?.last_run;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", fontFamily: FONT_MONO }}>
      {/* Header bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        fontSize: 11, letterSpacing: 1, color: C.dim,
        borderBottom: "1px solid rgba(0,255,0,0.2)",
        padding: "10px 16px", paddingBottom: 10,
      }}>
        <span style={{ color: C.green, textShadow: `0 0 5px ${C.green}` }}>synapse-deck</span>
        <span style={{ color: "var(--ns-bright)", border: "1px solid rgba(39,201,63,0.5)", padding: "0 7px" }}>
          ● localhost:7777
        </span>
        <span style={{ marginLeft: "auto" }}>standalone query service</span>
      </div>

      {/* Main area */}
      <div style={{
        flex: 1, display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", padding: "10px 0",
      }}>
        {/* Title */}
        <div style={{
          fontFamily: FONT_UI, fontWeight: 700,
          fontSize: 30, color: C.cyan,
          textShadow: `0 0 14px rgba(0,243,255,0.55)`,
          letterSpacing: 2, marginBottom: 20,
        }}>
          ASK THE VAULT
        </div>

        {/* Input */}
        <div style={{
          width: "92%", maxWidth: 620,
          display: "flex", alignItems: "center", gap: 12,
          border: `1px solid ${C.green}`,
          background: "rgba(0,0,0,0.6)",
          padding: "13px 18px",
          boxShadow: "0 0 22px rgba(0,255,0,0.18)",
        }}>
          <span style={{ color: C.purple, fontSize: 19 }}>$</span>
          <input
            role="textbox"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            style={{
              flex: 1, background: "transparent", border: "none", outline: "none",
              color: C.text, fontFamily: FONT_UI, fontSize: 16,
            }}
          />
          <span style={{
            width: 10, height: 21, background: C.purple,
            animation: "gv-blink 1s step-end infinite",
          }} />
        </div>

        {/* Chips */}
        <div style={{
          width: "92%", maxWidth: 620,
          display: "flex", gap: 9, marginTop: 12,
          fontSize: 11, color: C.dim,
          justifyContent: "center", flexWrap: "wrap",
        }}>
          {CHIPS.map((chip) => (
            <span
              key={chip}
              onClick={() => setQuery(chip)}
              style={{
                cursor: "pointer",
                border: "1px solid rgba(0,255,0,0.3)",
                padding: "3px 12px",
                color: C.text,
              }}
            >
              {chip}
            </span>
          ))}
          <span style={{ color: C.dim, alignSelf: "center" }}>↵ to run · ⌘K anywhere</span>
        </div>

        {/* Answer card */}
        <AnswerCard data={askData} isLoading={isLoading} isError={isError} activeQuery={activeQuery} />
      </div>

      {/* Footer stat tiles */}
      <div style={{ display: "flex", borderTop: "1px dashed rgba(0,255,0,0.3)" }}>
        <FooterTile
          label="NEXT DUE"
          value={nextDue ? `${nextDue.title} · in ${nextDue.days}d` : "—"}
          color={C.amber}
        />
        <FooterTile
          label="REVIEW"
          value={reviewCount > 0 ? `${reviewCount} items` : "—"}
          color={C.cyan}
        />
        <FooterTile
          label="LAST COMPILE"
          value={lastRun && compiled > 0 ? `${compiled} · ${lastRun.ts}` : "—"}
          color="var(--ns-bright)"
        />
        <div style={{ flex: 1, padding: "11px 14px" }}>
          <div style={{ color: C.dim, fontSize: 10, letterSpacing: 1 }}>VAULT</div>
          <div style={{ color: C.text, fontSize: 12, marginTop: 3 }}>
            {total > 0 ? `${total} entities` : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
