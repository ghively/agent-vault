// web/src/screens/Vault.tsx
// Agent Vault hub — orients the user and lets them open the vault sub-apps
// (Browse, Wiki, Pipeline, Review, Credentials, Command Deck, Settings)
// using the existing nav pattern.

import React from "react";
import { NsTitle, NsCard } from "../ui/Ns";
import { C, FONT_MONO, FONT_UI } from "../theme";

// ── Hub tile definition ───────────────────────────────────────────────────────

interface HubTile {
  id: string;
  glyph: string;
  name: string;
  desc: string;
  color: string;
}

const TILES: HubTile[] = [
  {
    id: "browse",
    glyph: "⊞",
    name: "BROWSE",
    desc: "search and filter vault entities",
    color: C.cyan,
  },
  {
    id: "wiki",
    glyph: "≣",
    name: "WIKI",
    desc: "read and navigate the household wiki",
    color: "#19e3d0",
  },
  {
    id: "pipeline",
    glyph: "≫",
    name: "PIPELINE",
    desc: "ingest → compile → promote pipeline",
    color: C.lilac,
  },
  {
    id: "review",
    glyph: "✓",
    name: "REVIEW",
    desc: "approve or reject pending items",
    color: C.amber,
  },
  {
    id: "creds",
    glyph: "⚷",
    name: "CREDENTIALS",
    desc: "credential references and resolvers",
    color: C.red,
  },
  {
    id: "command",
    glyph: "⌘",
    name: "COMMAND DECK",
    desc: "query the vault with natural language",
    color: "#4d9bff",
  },
  {
    id: "settings",
    glyph: "⚙",
    name: "SETTINGS",
    desc: "vault config & preferences",
    color: "#8a7dff",
  },
];

// ── AppTile ───────────────────────────────────────────────────────────────────

function AppTile({ tile, onOpen }: { tile: HubTile; onOpen: (id: string) => void }) {
  const [hovered, setHovered] = React.useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`vault-tile-${tile.id}`}
      onClick={() => onOpen(tile.id)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(tile.id); } }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: "14px 16px",
        cursor: "pointer",
        borderRadius: 11,
        overflow: "hidden",
        border: `1px solid ${hovered ? tile.color : "rgba(255,255,255,0.09)"}`,
        // color-mix works with both literal hex and var(--…) tokens — the old
        // hexToRgb() helper produced rgba(NaN,…) for CSS-variable colors.
        background: hovered
          ? `color-mix(in srgb, ${tile.color} 8%, transparent)`
          : "rgba(255,255,255,0.022)",
        transition: "all 0.15s cubic-bezier(0.25,1,0.5,1)",
        boxShadow: hovered ? `0 0 12px color-mix(in srgb, ${tile.color} 25%, transparent)` : "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            fontSize: 22,
            color: tile.color,
            width: 28,
            textAlign: "center",
            flexShrink: 0,
            filter: hovered ? `drop-shadow(0 0 6px ${tile.color})` : "none",
            transition: "filter 0.15s",
          }}
        >
          {tile.glyph}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              color: tile.color,
              fontFamily: FONT_UI,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: 1,
            }}
          >
            {tile.name}
          </div>
          <div
            style={{
              color: C.dim,
              fontFamily: FONT_MONO,
              fontSize: 11,
              marginTop: 3,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {tile.desc}
          </div>
        </div>
        <span
          style={{
            color: hovered ? tile.color : C.dim2,
            fontFamily: FONT_MONO,
            fontSize: 14,
            transition: "color 0.15s",
            flexShrink: 0,
          }}
        >
          →
        </span>
      </div>
    </div>
  );
}

// ── Vault hub screen ──────────────────────────────────────────────────────────

interface VaultProps {
  onNavigate: (screen: string) => void;
}

export function Vault({ onNavigate }: VaultProps) {
  return (
    <div
      style={{
        padding: "12px 16px",
        color: C.text,
        fontFamily: FONT_MONO,
        fontSize: 12,
        height: "100%",
        boxSizing: "border-box",
        overflow: "auto",
      }}
    >
      <NsTitle sub="root@synapse:~$ vault — household knowledge base">
        Agent Vault
      </NsTitle>

      {/* Blurb */}
      <NsCard style={{ padding: "10px 14px", marginBottom: 18 }}>
        <div style={{ color: C.mutedGreen, fontSize: 11, lineHeight: 1.6 }}>
          Agent Vault is a deterministic, file-based knowledge wiki for your household.
          It ingests documents, compiles entities, tracks credentials and policies,
          and answers questions via the Command Deck. Open any module below.
        </div>
      </NsCard>

      {/* Hub grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 10,
        }}
        data-testid="vault-hub-grid"
      >
        {TILES.map((tile) => (
          <AppTile key={tile.id} tile={tile} onOpen={onNavigate} />
        ))}
      </div>
    </div>
  );
}
