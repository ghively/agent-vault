// web/src/wm/apps.ts
// App registry for Agent Vault PHOSPHOR shell.
// Ported from SynapseNAS, stripped to the 7 vault apps.

export type AppId =
  | "browse"
  | "wiki"
  | "vault"
  | "creds"
  | "review"
  | "pipeline"
  | "command";

export interface AppDef {
  glyph: string; name: string; label: string; title: string;
  desc: string; color: string; grad: string; w: number; h: number;
}

// Ported from design/Agent Vault GUI.dc.html `this.apps`.
export const APPS: Record<AppId, AppDef> = {
  browse: {
    glyph: "⊕", name: "BROWSE", label: "Browse", title: "vault · browse",
    desc: "browse vault entities", color: "#00f3ff",
    grad: "linear-gradient(145deg,#22eeff,#0a5060)", w: 960, h: 580,
  },
  wiki: {
    glyph: "⊛", name: "WIKI", label: "Wiki", title: "vault · wiki",
    desc: "document wiki", color: "#27c93f",
    grad: "linear-gradient(145deg,#2fe06a,#0a6b2e)", w: 1040, h: 640,
  },
  vault: {
    glyph: "◆", name: "VAULT", label: "Vault", title: "vault · vault",
    desc: "vault hub", color: "#4d9bff",
    grad: "linear-gradient(145deg,#5aa6ff,#1030a0)", w: 920, h: 560,
  },
  creds: {
    glyph: "⊘", name: "CREDS", label: "Credentials", title: "vault · creds",
    desc: "credentials & secrets", color: "#ffbd2e",
    grad: "linear-gradient(145deg,#ffd060,#8a5800)", w: 980, h: 600,
  },
  review: {
    glyph: "⊟", name: "REVIEW", label: "Review", title: "vault · review",
    desc: "review queue", color: "#ff5f56",
    grad: "linear-gradient(145deg,#ff7070,#8a1010)", w: 1020, h: 620,
  },
  pipeline: {
    glyph: "⊠", name: "PIPELINE", label: "Pipeline", title: "vault · pipeline",
    desc: "ingest pipeline", color: "#b266ff",
    grad: "linear-gradient(145deg,#c080ff,#5000c0)", w: 1060, h: 660,
  },
  command: {
    glyph: "⌨", name: "COMMAND", label: "Command Deck", title: "vault · command",
    desc: "command deck", color: "#8000ff",
    grad: "linear-gradient(145deg,#9020ff,#380080)", w: 1060, h: 660,
  },
};

export const APP_ORDER: AppId[] = [
  "browse", "wiki", "vault", "creds", "review", "pipeline", "command",
];

// Launcher grouping — all vault apps in a single group
export interface AppGroup {
  label: string;
  ids: AppId[];
}

export const APP_GROUPS: AppGroup[] = [
  {
    label: "VAULT",
    ids: ["browse", "wiki", "vault", "creds", "review", "pipeline", "command"],
  },
];

export const ICON_FILE: Record<AppId, string> = {
  browse: "browse",
  wiki: "wiki",
  vault: "vault",
  creds: "creds",
  review: "review",
  pipeline: "pipeline",
  command: "command",
};
