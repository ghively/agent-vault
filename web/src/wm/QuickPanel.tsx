import React, { useState, useEffect, useRef } from "react";
import { useWindows, SCALE_MIN, SCALE_MAX } from "../store/windows";
import { useConfig, useStatus } from "../api/hooks";
import { useApplyConfig } from "../api/mutations";

export function QuickPanel() {
  const togglePanel = useWindows((s) => s.togglePanel);
  const openApp = useWindows((s) => s.openApp);
  const desktopScale = useWindows((s) => s.desktopScale);
  const setDesktopScale = useWindows((s) => s.setDesktopScale);
  const theme = useWindows((s) => s.theme);
  const setTheme = useWindows((s) => s.setTheme);
  const density = useWindows((s) => s.density);
  const setDensity = useWindows((s) => s.setDensity);
  const config = useConfig();
  const status = useStatus();
  const applyConfig = useApplyConfig();

  const entities = status.data?.counts?.total ?? 0;
  const types = Object.keys(status.data?.breakdown ?? {}).length;

  // Live vault reachability, derived from the /api/status query state — not a
  // hardcoded "ONLINE" badge.
  const vaultState = status.isError
    ? { label: "● OFFLINE", color: "#ff5f56" }
    : status.data
      ? { label: "● ONLINE", color: "#27c93f" }
      : { label: "○ CHECKING", color: "#888" };

  // Local editable state — synced from config once loaded
  const [compiler, setCompilerState] = useState<string>(
    config.data?.env?.AGENT_VAULT_COMPILER ?? "mock"
  );
  const [perSource, setPerSourceState] = useState<number>(
    Number(config.data?.env?.AGENT_VAULT_PER_SOURCE_CHARS ?? "4000")
  );
  const [maxRawMb, setMaxRawMbState] = useState<number>(
    Number(config.data?.env?.AGENT_VAULT_MAX_RAW_MB ?? "50")
  );
  // Last values committed to the server — lets keyboard commit handlers
  // (onBlur/onKeyUp) fire freely without posting duplicate patches.
  const committed = useRef<{ perSource: number; maxRawMb: number }>({
    perSource: 4000,
    maxRawMb: 50,
  });

  useEffect(() => {
    if (!config.data) return;
    const env = config.data.env ?? {};
    setCompilerState(env.AGENT_VAULT_COMPILER ?? "mock");
    const per = Number(env.AGENT_VAULT_PER_SOURCE_CHARS ?? "4000");
    const raw = Number(env.AGENT_VAULT_MAX_RAW_MB ?? "50");
    setPerSourceState(per);
    setMaxRawMbState(raw);
    committed.current = { perSource: per, maxRawMb: raw };
  }, [config.data]);

  const resolvers = config.data?.resolvers ?? [];

  function applyEnvPatch(patch: Record<string, string>) {
    if (!config.data?.env) return; // still guard pre-load
    applyConfig.mutate({ env: patch }); // send ONLY changed keys; backend merges against disk
  }

  function handleCompilerToggle(opt: string) {
    if (opt === compiler) return;
    setCompilerState(opt);
    applyEnvPatch({ AGENT_VAULT_COMPILER: opt });
  }

  function commitPerSource(v: number) {
    if (v === committed.current.perSource) return;
    committed.current.perSource = v;
    setPerSourceState(v);
    applyEnvPatch({ AGENT_VAULT_PER_SOURCE_CHARS: String(v) });
  }

  function commitMaxRawMb(v: number) {
    if (v === committed.current.maxRawMb) return;
    committed.current.maxRawMb = v;
    setMaxRawMbState(v);
    applyEnvPatch({ AGENT_VAULT_MAX_RAW_MB: String(v) });
  }

  return (
    <div
      style={{
        position: "absolute",
        top: 54,
        right: 8,
        width: 312,
        zIndex: 7500,
        background: "rgba(0,10,6,0.92)",
        border: "1px solid rgba(0,255,0,0.3)",
        borderRadius: 12,
        backdropFilter: "blur(14px)",
        boxShadow: "0 12px 40px rgba(0,0,0,0.6),0 0 20px rgba(0,255,0,0.08)",
        padding: 16,
        animation: "gv-pop 0.18s ease-out",
        fontFamily: "'Space Grotesk',system-ui,sans-serif",
        fontSize: 12,
      }}
    >
      {/* profile */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 10,
          border: "1px solid rgba(0,255,0,0.4)", background: "rgba(0,20,0,0.6)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontWeight: 700, letterSpacing: -0.5, fontSize: 24,
          color: "#00ff00", textShadow: "0 0 8px #00ff00",
        }}>G</div>
        <div style={{ flex: 1 }}>
          <div style={{ color: "#ccffcc", fontSize: 13 }}>root@synapse</div>
          <div style={{ color: "#666", fontSize: 11 }}>uptime — · schema v1</div>
        </div>
        <div style={{ display: "flex", gap: 8, color: "#888", fontSize: 14 }}>
          <span aria-hidden style={{ cursor: "default" }} title="not implemented yet">⏻</span>
          <span aria-hidden style={{ cursor: "default" }} title="not implemented yet">⟲</span>
          <button
            type="button"
            aria-label="Collapse panel"
            onClick={togglePanel}
            style={{ cursor: "pointer", background: "transparent", border: "none",
                     color: "inherit", font: "inherit", padding: 0 }}
          >
            ⏷
          </button>
        </div>
      </div>

      {/* vault status */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <div style={{
          flex: 1, border: "1px solid rgba(0,255,0,0.2)", background: "rgba(0,0,0,0.4)",
          borderRadius: 8, padding: "8px 10px",
        }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: 1 }}>VAULT</div>
          <div style={{ color: vaultState.color, fontSize: 12 }}>{vaultState.label}</div>
        </div>
        <div style={{
          flex: 1, border: "1px solid rgba(0,255,0,0.2)", background: "rgba(0,0,0,0.4)",
          borderRadius: 8, padding: "8px 10px",
        }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: 1 }}>ENTITIES</div>
          <div style={{ color: "#ccffcc", fontSize: 12 }}>
            {status.data ? `${entities} · ${types} types` : "—"}
          </div>
        </div>
      </div>

      {/* desktop scale (resolution) */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
          <span style={{ color: "#ccffcc" }}>🖥 desktop scale</span>
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ color: "#00f3ff" }}>{Math.round(desktopScale * 100)}%</span>
            <span
              onClick={() => setDesktopScale(1)}
              title="reset to 100%"
              style={{ cursor: "pointer", color: "#666", fontSize: 11 }}
            >
              ⟲
            </span>
          </span>
        </div>
        <input
          type="range"
          min={SCALE_MIN}
          max={SCALE_MAX}
          step={0.05}
          value={desktopScale}
          className="gv-rng"
          style={{ width: "100%" }}
          aria-label="desktop scale"
          onChange={(e) => setDesktopScale(Number(e.target.value))}
        />
      </div>

      {/* theme toggle */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ color: "#888", fontSize: 11, marginBottom: 6, letterSpacing: 1 }}>THEME</div>
        <div style={{ display: "flex", gap: 7 }}>
          {(["dark", "light"] as const).map((th) => {
            const active = theme === th;
            return (
              <button
                type="button"
                key={th}
                aria-pressed={active}
                onClick={() => setTheme(th)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12,
                  cursor: "pointer", font: "inherit",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {th}
              </button>
            );
          })}
        </div>
      </div>

      {/* density toggle */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ color: "#888", fontSize: 11, marginBottom: 6, letterSpacing: 1 }}>DENSITY</div>
        <div style={{ display: "flex", gap: 7 }}>
          {(["comfortable", "compact"] as const).map((dens) => {
            const active = density === dens;
            return (
              <button
                type="button"
                key={dens}
                aria-pressed={active}
                onClick={() => setDensity(dens)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12,
                  cursor: "pointer", font: "inherit",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {dens}
              </button>
            );
          })}
        </div>
      </div>

      {/* compiler toggle */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ color: "#888", fontSize: 11, marginBottom: 6, letterSpacing: 1 }}>COMPILE BACKEND</div>
        <div style={{ display: "flex", gap: 7 }}>
          {["mock", "ollama"].map((opt) => {
            const active = compiler === opt;
            return (
              <button
                type="button"
                key={opt}
                aria-pressed={active}
                onClick={() => handleCompilerToggle(opt)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12,
                  cursor: "pointer", font: "inherit",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {opt}
              </button>
            );
          })}
        </div>
      </div>

      {/* source budget slider */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
          <span style={{ color: "#ccffcc" }}>⛁ source budget</span>
          <span style={{ color: "#00f3ff" }}>{perSource}</span>
        </div>
        <input
          type="range" min={1000} max={8000} step={500}
          value={perSource}
          className="gv-rng"
          style={{ width: "100%" }}
          aria-label="source budget"
          onChange={(e) => setPerSourceState(Number(e.target.value))}
          onMouseUp={(e) => commitPerSource(Number((e.target as HTMLInputElement).value))}
          onTouchEnd={(e) => commitPerSource(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => commitPerSource(Number((e.target as HTMLInputElement).value))}
          onBlur={(e) => commitPerSource(Number(e.target.value))}
        />
      </div>

      {/* max raw MB slider */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
          <span style={{ color: "#ccffcc" }}>⛃ max raw MB</span>
          <span style={{ color: "#00f3ff" }}>{maxRawMb}</span>
        </div>
        <input
          type="range" min={10} max={200} step={5}
          value={maxRawMb}
          className="gv-rng"
          style={{ width: "100%" }}
          aria-label="max raw MB"
          onChange={(e) => setMaxRawMbState(Number(e.target.value))}
          onMouseUp={(e) => commitMaxRawMb(Number((e.target as HTMLInputElement).value))}
          onTouchEnd={(e) => commitMaxRawMb(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => commitMaxRawMb(Number((e.target as HTMLInputElement).value))}
          onBlur={(e) => commitMaxRawMb(Number(e.target.value))}
        />
      </div>

      {applyConfig.isError && (
        <div style={{ color: "#ff5f56", fontSize: 11, marginBottom: 10 }}>
          config apply failed: {applyConfig.error instanceof Error ? applyConfig.error.message : "unknown error"}
        </div>
      )}

      {/* resolver backends — display-only (configured via resolvers.yaml) */}
      <div style={{ color: "#888", fontSize: 11, marginBottom: 7, letterSpacing: 1 }}>RESOLVERS</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7, marginBottom: 14 }}>
        {resolvers.length === 0 ? (
          <div style={{ color: "#444", fontSize: 11 }}>no resolvers configured</div>
        ) : resolvers.map((r, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ color: "#ccffcc", fontSize: 12, flex: 1 }}>{r.name}</span>
            <span style={{ color: "#555", fontSize: 10 }}>{r.detail}</span>
          </div>
        ))}
      </div>

      {/* open settings */}
      <button
        type="button"
        onClick={() => openApp("vault")}
        style={{
          cursor: "pointer", display: "block", width: "100%", textAlign: "center",
          fontSize: 12, padding: 9, borderRadius: 8, font: "inherit",
          background: "transparent",
          border: "1px solid rgba(0,255,0,0.4)", color: "#00ff00",
          textShadow: "0 0 5px #00ff00",
        }}
      >
        ◆ Open Agent Vault
      </button>
    </div>
  );
}
