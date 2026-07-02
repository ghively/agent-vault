import React, { useState, useEffect } from "react";
import { useWindows, SCALE_MIN, SCALE_MAX } from "../store/windows";
import { useConfig, useStatus } from "../api/hooks";
import { useApplyConfig } from "../api/mutations";
import { useT } from "../i18n";
import i18n from "i18next";

export function QuickPanel() {
  const togglePanel = useWindows((s) => s.togglePanel);
  const openApp = useWindows((s) => s.openApp);
  const desktopScale = useWindows((s) => s.desktopScale);
  const setDesktopScale = useWindows((s) => s.setDesktopScale);
  const theme = useWindows((s) => s.theme);
  const setTheme = useWindows((s) => s.setTheme);
  const density = useWindows((s) => s.density);
  const setDensity = useWindows((s) => s.setDensity);
  const locale = useWindows((s) => s.locale);
  const setLocale = useWindows((s) => s.setLocale);
  const config = useConfig();
  const status = useStatus();
  const applyConfig = useApplyConfig();

  const entities = status.data?.counts?.total ?? 0;
  const types = Object.keys(status.data?.breakdown ?? {}).length;

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

  useEffect(() => {
    if (!config.data) return;
    const env = config.data.env ?? {};
    setCompilerState(env.AGENT_VAULT_COMPILER ?? "mock");
    setPerSourceState(Number(env.AGENT_VAULT_PER_SOURCE_CHARS ?? "4000"));
    setMaxRawMbState(Number(env.AGENT_VAULT_MAX_RAW_MB ?? "50"));
  }, [config.data]);

  // Resolvers used as cadence display
  const cadences = config.data?.resolvers ?? [];

  function applyEnvPatch(patch: Record<string, string>) {
    if (!config.data?.env) return; // still guard pre-load
    applyConfig.mutate({ env: patch }); // send ONLY changed keys; backend merges against disk
  }

  function handleCompilerToggle(opt: string) {
    setCompilerState(opt);
    applyEnvPatch({ AGENT_VAULT_COMPILER: opt });
  }

  function handlePerSourceCommit(v: number) {
    setPerSourceState(v);
    applyEnvPatch({ AGENT_VAULT_PER_SOURCE_CHARS: String(v) });
  }

  function handleMaxRawMbCommit(v: number) {
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
          <span style={{ cursor: "default" }} title="enabled in a later milestone">⏻</span>
          <span style={{ cursor: "default" }} title="enabled in a later milestone">⟲</span>
          <span style={{ cursor: "pointer" }} onClick={togglePanel}>⏷</span>
        </div>
      </div>

      {/* vault status */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <div style={{
          flex: 1, border: "1px solid rgba(0,255,0,0.2)", background: "rgba(0,0,0,0.4)",
          borderRadius: 8, padding: "8px 10px",
        }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: 1 }}>VAULT</div>
          <div style={{ color: "#27c93f", fontSize: 12 }}>● ONLINE</div>
        </div>
        <div style={{
          flex: 1, border: "1px solid rgba(0,255,0,0.2)", background: "rgba(0,0,0,0.4)",
          borderRadius: 8, padding: "8px 10px",
        }}>
          <div style={{ color: "#666", fontSize: 9, letterSpacing: 1 }}>ENTITIES</div>
          <div style={{ color: "#ccffcc", fontSize: 12 }}>{entities} · {types} types</div>
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
              <span
                key={th}
                onClick={() => setTheme(th)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12, cursor: "pointer",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {th}
              </span>
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
              <span
                key={dens}
                onClick={() => setDensity(dens)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12, cursor: "pointer",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {dens}
              </span>
            );
          })}
        </div>
      </div>

      {/* locale selector */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ color: "#888", fontSize: 11, marginBottom: 6, letterSpacing: 1 }}>LOCALE</div>
        <div style={{ display: "flex", gap: 7 }}>
          {(["en"] as const).map((loc) => {
            const active = locale === loc;
            return (
              <span
                key={loc}
                onClick={() => {
                  setLocale(loc);
                  i18n.changeLanguage(loc);
                }}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12, cursor: "pointer",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {loc.toUpperCase()}
              </span>
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
              <span
                key={opt}
                onClick={() => handleCompilerToggle(opt)}
                style={{
                  flex: 1, textAlign: "center", padding: 7, borderRadius: 8, fontSize: 12, cursor: "pointer",
                  border: `1px solid ${active ? "rgba(0,255,0,0.6)" : "rgba(0,255,0,0.18)"}`,
                  color: active ? "#00ff00" : "#555",
                  background: active ? "rgba(0,255,0,0.12)" : "rgba(0,0,0,0.3)",
                }}
              >
                {opt}
              </span>
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
          onChange={(e) => setPerSourceState(Number(e.target.value))}
          onMouseUp={(e) => handlePerSourceCommit(Number((e.target as HTMLInputElement).value))}
          onTouchEnd={(e) => handlePerSourceCommit(Number((e.target as HTMLInputElement).value))}
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
          onChange={(e) => setMaxRawMbState(Number(e.target.value))}
          onMouseUp={(e) => handleMaxRawMbCommit(Number((e.target as HTMLInputElement).value))}
          onTouchEnd={(e) => handleMaxRawMbCommit(Number((e.target as HTMLInputElement).value))}
        />
      </div>

      {/* cadence toggles — display-only (resolvers are configured via resolvers.yaml) */}
      <div style={{ color: "#888", fontSize: 11, marginBottom: 7, letterSpacing: 1 }}>CADENCES</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7, marginBottom: 14 }}>
        {cadences.length === 0 ? (
          <div style={{ color: "#444", fontSize: 11 }}>no resolvers configured</div>
        ) : cadences.map((c, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                cursor: "default", width: 36, height: 19, borderRadius: 10,
                border: "1px solid rgba(0,255,0,0.2)", background: "rgba(0,0,0,0.4)",
                position: "relative", flex: "none",
              }}
            >
              <span style={{
                position: "absolute", top: 1, left: 2, width: 15, height: 15,
                borderRadius: "50%", background: "#444",
              }} />
            </span>
            <span style={{ color: "#ccffcc", fontSize: 12, flex: 1 }}>{c.name}</span>
            <span style={{ color: "#555", fontSize: 10 }}>{c.detail}</span>
          </div>
        ))}
      </div>

      {/* current job card */}
      <div style={{
        border: "1px solid rgba(128,0,255,0.4)", background: "rgba(8,0,14,0.5)",
        borderRadius: 10, padding: "11px 13px", marginBottom: 12,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span style={{
            width: 34, height: 34, borderRadius: 8, background: "rgba(0,243,255,0.12)",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "#00f3ff", fontSize: 16,
          }}>▸</span>
          <div style={{ flex: 1 }}>
            <div style={{ color: "#ccffcc", fontSize: 12 }}>weekly.sh — compile</div>
            <div style={{ color: "#666", fontSize: 10 }}>no job running</div>
          </div>
          <span style={{ color: "#27c93f", fontSize: 11 }}>—</span>
        </div>
        <div style={{ height: 5, borderRadius: 3, background: "rgba(0,0,0,0.5)", marginTop: 9, position: "relative", overflow: "hidden" }}>
          <span style={{
            position: "absolute", left: 0, top: 0, bottom: 0, width: "0%",
            background: "linear-gradient(90deg,#00f3ff,#8000ff)",
          }} />
        </div>
      </div>

      {/* open settings */}
      <span
        onClick={() => openApp("vault")}
        style={{
          cursor: "pointer", display: "block", textAlign: "center",
          fontSize: 12, padding: 9, borderRadius: 8,
          border: "1px solid rgba(0,255,0,0.4)", color: "#00ff00",
          textShadow: "0 0 5px #00ff00",
        }}
      >
        ◆ Open Agent Vault
      </span>
    </div>
  );
}
