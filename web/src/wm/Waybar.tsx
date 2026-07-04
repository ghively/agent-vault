import React, { useEffect, useRef } from "react";
import { useWindows, focusedApp } from "../store/windows";
import { APPS } from "./apps";
import { useReviewProposals, useReviewEntities, useConfig } from "../api/hooks";

function now() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

export function Waybar() {
  const clockRef = useRef<HTMLSpanElement>(null);
  const open = useWindows((s) => s.open);
  const focus = useWindows((s) => s.focus);
  const toggleLauncher = useWindows((s) => s.toggleLauncher);
  const togglePanel = useWindows((s) => s.togglePanel);
  const openApp = useWindows((s) => s.openApp);
  const minimized = useWindows((s) => s.minimized);
  const focused = useWindows(focusedApp);

  const proposals = useReviewProposals();
  const entities = useReviewEntities();
  const config = useConfig();

  // Show "!" on an API error rather than a confident "0", so a broken endpoint
  // reads as unknown instead of "nothing to review" (F6).
  const reviewCount = proposals.isError ? "!" : proposals.data?.items?.length ?? 0;
  const entityCount = entities.isError ? "!" : entities.data?.items?.length ?? 0;
  const compilerLabel = config.data?.env?.AGENT_VAULT_COMPILER ?? "mock";

  useEffect(() => {
    const tick = setInterval(() => {
      if (clockRef.current) clockRef.current.textContent = now();
    }, 1000);
    return () => clearInterval(tick);
  }, []);

  const switcher = open.map((id) => {
    const a = APPS[id];
    const isOn = id === focused;
    const isMin = !!minimized[id];
    return {
      id,
      glyph: a.glyph,
      name: a.name,
      isMin,
      bg: isOn ? "rgba(0,255,0,0.16)" : "rgba(0,0,0,0.25)",
      border: isOn ? "rgba(0,255,0,0.4)" : "transparent",
      color: isOn ? "#00ff00" : "#aaa",
    };
  });

  // Use the short glyph+name for the center display (not the full title path)
  // to avoid duplicate text with the window title bar chrome
  const focusedTitle = focused ? `${APPS[focused].glyph} ${APPS[focused].name}` : "";

  return (
    <div style={{
      position: "absolute", top: 8, left: 8, right: 8, height: 38, zIndex: 8000,
      display: "flex", alignItems: "center", gap: 8, padding: "0 8px",
      background: "rgba(0,12,4,0.82)", border: "1px solid rgba(0,255,0,0.22)",
      borderRadius: 10, backdropFilter: "blur(10px)",
      boxShadow: "0 4px 16px rgba(0,0,0,0.4)", fontSize: 12,
    }}>
      {/* launcher pill */}
      <span
        role="button"
        tabIndex={0}
        aria-label="Open application launcher"
        onClick={toggleLauncher}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleLauncher(); } }}
        className="gv-pill"
        style={{
          cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
          padding: "5px 11px", borderRadius: 7, background: "rgba(0,243,255,0.12)",
          color: "#00f3ff", letterSpacing: 1, textShadow: "0 0 5px #00f3ff",
        }}
      >◴ APPS</span>

      <span style={{ color: "#00ff00", textShadow: "0 0 6px #00ff00", fontWeight: "bold", letterSpacing: 1 }}>
        [GV]
      </span>

      {/* window switcher pills */}
      <div style={{ display: "flex", alignItems: "center", gap: 5, marginLeft: 4 }}>
        {switcher.map((w) => (
          <span
            key={w.id}
            role="button"
            tabIndex={0}
            aria-label={w.isMin ? `Restore ${w.name}` : `Focus ${w.name}`}
            onClick={() => focus(w.id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focus(w.id); } }}
            className="gv-pill"
            title={w.isMin ? "restore window" : undefined}
            style={{
              cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
              padding: "4px 10px", borderRadius: 7,
              background: w.bg, border: `1px solid ${w.border}`, color: w.color,
              opacity: w.isMin ? 0.55 : 1,
            }}
          >
            <span style={{ fontFamily: "'Space Grotesk',system-ui,sans-serif", fontWeight: 700, letterSpacing: -0.5, fontSize: 14 }}>
              {w.glyph}
            </span>
            {w.name}
          </span>
        ))}
      </div>

      {/* center: focused title */}
      <span style={{
        flex: 1, textAlign: "center", color: "#ccffcc", letterSpacing: 1, opacity: 0.8,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 40,
      }}>
        {focusedTitle}
      </span>

      {/* right modules */}
      <div style={{ display: "flex", alignItems: "center", gap: 7, flex: "none" }}>
        {/* review counter */}
        <span className="gv-pill" style={{ padding: "5px 10px", borderRadius: 7, background: "rgba(0,243,255,0.12)", color: "#00f3ff" }}>
          ◈ {reviewCount}
        </span>

        {/* entity flags */}
        <span className="gv-pill" style={{ padding: "5px 10px", borderRadius: 7, background: "rgba(255,189,46,0.14)", color: "#ffbd2e" }}>
          ⚑ {entityCount}
        </span>

        {/* compiler pill */}
        <span
          role="button"
          tabIndex={0}
          aria-label="Open vault hub (compiler settings)"
          onClick={() => openApp("vault")}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openApp("vault"); } }}
          className="gv-pill"
          style={{ cursor: "pointer", padding: "5px 10px", borderRadius: 7, background: "rgba(128,0,255,0.16)", color: "#b266ff" }}
        >
          ⚙ {compilerLabel}
        </span>

        {/* AI Assistant button */}
        <span
          role="button"
          tabIndex={0}
          aria-label="Open the Command Deck assistant"
          onClick={() => openApp("command")}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openApp("command"); } }}
          className="gv-pill"
          style={{
            cursor: "pointer",
            padding: "5px 10px",
            borderRadius: 7,
            background: "rgba(178, 102, 255, 0.16)",
            color: "#b266ff",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          <span>✦</span>
          <span>ASSISTANT</span>
        </span>

        {/* panel toggle */}
        <span
          role="button"
          tabIndex={0}
          aria-label="Toggle quick settings panel"
          onClick={togglePanel}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); togglePanel(); } }}
          className="gv-pill"
          style={{ cursor: "pointer", padding: "5px 10px", borderRadius: 7, background: "rgba(0,0,0,0.3)", color: "#ccffcc" }}
        >▤</span>

        {/* clock */}
        <span ref={clockRef} style={{
          padding: "5px 11px", borderRadius: 7, background: "rgba(0,20,0,0.5)", color: "#ccffcc", letterSpacing: 1,
        }}>
          {now()}
        </span>
      </div>
    </div>
  );
}
