// web/src/screens/Settings.tsx
// Surfaces the vault's configuration (service/vault/compiler/resolvers/cadences)
// from GET /api/settings, plus the client-side UI preferences (theme/density/scale).

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { NsTitle, NsCard } from "../ui/Ns";
import { C, FONT_MONO, FONT_UI } from "../theme";
import { vaultFetch } from "../api/client";
import { useWindows, SCALE_MIN, SCALE_MAX } from "../store/windows";

interface Backend {
  scheme: string;
  module: string;
  description: string;
}
interface SettingsData {
  service: { version: string; vault_path: string; host: string; port: number; auth_enabled: boolean };
  vault: { entity_count: number; index_built: boolean };
  compiler: { prompt_contract_version: string; ollama_model: string };
  resolvers: { default: string | null; backends: Backend[] };
  cadences: string[];
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: "5px 0", borderBottom: "1px solid rgba(255,255,255,0.05)", fontFamily: FONT_MONO, fontSize: 12 }}>
      <span style={{ color: C.dim }}>{k}</span>
      <span style={{ color: C.text, textAlign: "right", wordBreak: "break-all" }}>{v}</span>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <NsCard style={{ padding: "12px 14px", marginBottom: 14 }}>
      <div style={{ color: C.cyan, fontFamily: FONT_UI, fontSize: 12, fontWeight: 700, letterSpacing: 1, marginBottom: 8, borderBottom: `1px solid ${C.purple}`, paddingBottom: 5 }}>{title}</div>
      {children}
    </NsCard>
  );
}

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: "5px 12px", borderRadius: 6, cursor: "pointer", fontFamily: FONT_MONO, fontSize: 12,
      border: `1px solid ${active ? C.green : "rgba(255,255,255,0.12)"}`,
      background: active ? "rgba(0,255,0,0.08)" : "transparent",
      color: active ? C.green : C.dim,
    }}>{children}</button>
  );
}

export function Settings() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["settings"],
    queryFn: () => vaultFetch<SettingsData>("/api/settings"),
  });

  const theme = useWindows((s) => s.theme);
  const density = useWindows((s) => s.density);
  const scale = useWindows((s) => s.desktopScale);
  const setTheme = useWindows((s) => s.setTheme);
  const setDensity = useWindows((s) => s.setDensity);
  const setDesktopScale = useWindows((s) => s.setDesktopScale);

  return (
    <div style={{ padding: "12px 16px", color: C.text, fontFamily: FONT_MONO, fontSize: 12, height: "100%", boxSizing: "border-box", overflow: "auto" }}>
      <NsTitle sub="root@synapse:~$ settings">Agent Vault · Settings</NsTitle>

      {isLoading && <div style={{ color: C.dim }}>Loading settings…</div>}
      {isError && <div style={{ color: C.red }}>Failed to load settings.</div>}

      {data && (
        <>
          <Card title="SERVICE">
            <Row k="version" v={data.service.version} />
            <Row k="vault path" v={data.service.vault_path} />
            <Row k="listen" v={`${data.service.host}:${data.service.port}`} />
            <Row k="auth" v={data.service.auth_enabled ? "token (bearer)" : "open (no token)"} />
          </Card>

          <Card title="VAULT">
            <Row k="entities" v={data.vault.entity_count} />
            <Row k="index" v={data.vault.index_built ? "built" : "not built"} />
          </Card>

          <Card title="COMPILER">
            <Row k="prompt contract" v={data.compiler.prompt_contract_version} />
            <Row k="ollama model" v={data.compiler.ollama_model || "(env OLLAMA_MODEL not set)"} />
          </Card>

          <Card title="CREDENTIAL RESOLVERS">
            <Row k="default" v={data.resolvers.default ?? "(none)"} />
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
              {data.resolvers.backends.map((b) => (
                <div key={b.scheme} style={{ padding: "4px 0", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                  <span style={{ color: C.cyan }}>{b.scheme}://</span>
                  <span style={{ color: C.dim }}> — {b.description}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card title="CADENCES">
            {data.cadences.length === 0 ? (
              <div style={{ color: C.dim }}>(none)</div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {data.cadences.map((c) => (
                  <span key={c} style={{ padding: "3px 8px", borderRadius: 5, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}>{c}</span>
                ))}
              </div>
            )}
          </Card>
        </>
      )}

      <Card title="UI PREFERENCES">
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 12 }}>
          <span style={{ color: C.dim, minWidth: 80 }}>theme</span>
          <Pill active={theme === "dark"} onClick={() => setTheme("dark")}>dark</Pill>
          <Pill active={theme === "light"} onClick={() => setTheme("light")}>light</Pill>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 12 }}>
          <span style={{ color: C.dim, minWidth: 80 }}>density</span>
          <Pill active={density === "comfortable"} onClick={() => setDensity("comfortable")}>comfortable</Pill>
          <Pill active={density === "compact"} onClick={() => setDensity("compact")}>compact</Pill>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ color: C.dim, minWidth: 80 }}>scale</span>
          <input type="range" min={SCALE_MIN} max={SCALE_MAX} step={0.05} value={scale} onChange={(e) => setDesktopScale(parseFloat(e.target.value))} className="gv-rng" style={{ flex: 1 }} />
          <span style={{ color: C.cyan, minWidth: 48, textAlign: "right" }}>{Math.round(scale * 100)}%</span>
        </div>
      </Card>
    </div>
  );
}
