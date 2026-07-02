import React, { useEffect, useRef } from "react";
import { useWindows } from "../store/windows";
import { APP_GROUPS, APPS, type AppId } from "./apps";
import { AppIcon } from "./AppIcon";

export function Launcher() {
  const launcherOpen = useWindows((s) => s.launcherOpen);
  const launcherQuery = useWindows((s) => s.launcherQuery);
  const open = useWindows((s) => s.open);
  const openApp = useWindows((s) => s.openApp);
  const toggleLauncher = useWindows((s) => s.toggleLauncher);
  const setLauncherQuery = useWindows((s) => s.setLauncherQuery);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (launcherOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [launcherOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && launcherOpen) toggleLauncher();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [launcherOpen, toggleLauncher]);

  if (!launcherOpen) return null;

  const lq = launcherQuery.trim().toLowerCase();
  const matches = (id: AppId) => {
    if (!lq) return true;
    const a = APPS[id];
    return (id + " " + a.name + " " + a.label + " " + a.desc).toLowerCase().includes(lq);
  };
  // Filter within each group; drop groups that have no match for the query.
  const groups = APP_GROUPS.map((g) => ({ label: g.label, ids: g.ids.filter(matches) })).filter(
    (g) => g.ids.length > 0,
  );

  return (
    <div
      onClick={toggleLauncher}
      style={{
        position: "absolute", inset: 0, zIndex: 7800,
        background: "rgba(2,5,4,0.34)",
        backdropFilter: "blur(22px) saturate(115%)",
        WebkitBackdropFilter: "blur(22px) saturate(115%)",
        display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 58,
      }}
    >
      {/* search box */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 520, maxWidth: "78%", display: "flex", alignItems: "center", gap: 12,
          background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)",
          borderRadius: 14, padding: "14px 18px", boxShadow: "0 12px 40px rgba(0,0,0,0.4)",
        }}
      >
        <span style={{ color: "#19e3d0", fontSize: 18 }}>⌕</span>
        <input
          ref={inputRef}
          value={launcherQuery}
          onChange={(e) => setLauncherQuery(e.target.value)}
          placeholder="Search applications"
          style={{
            flex: 1, background: "transparent", border: "none", outline: "none",
            color: "#eafff0", fontFamily: "'Space Grotesk',system-ui,sans-serif", fontSize: 17,
          }}
        />
        <span style={{
          color: "#8aa", fontSize: 11,
          border: "1px solid rgba(255,255,255,0.14)", borderRadius: 6, padding: "2px 8px",
        }}>esc</span>
      </div>

      {/* grouped app grid */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          marginTop: 40, width: 820, maxWidth: "92%",
          display: "flex", flexDirection: "column", gap: 26,
          maxHeight: "70vh", overflowY: "auto", paddingRight: 4,
        }}
      >
        {groups.map((g) => (
          <section key={g.label}>
            <div style={{
              color: "#8fb6a0", fontSize: 11, fontWeight: 600, letterSpacing: 1.5,
              textTransform: "uppercase", marginBottom: 14, paddingLeft: 2,
              borderBottom: "1px solid rgba(255,255,255,0.08)", paddingBottom: 6,
            }}>
              {g.label}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "30px 18px" }}>
              {g.ids.map((id) => {
                const a = APPS[id];
                const isOpen = open.includes(id);
                return (
                  <div
                    key={id}
                    role="button"
                    tabIndex={0}
                    aria-label={a.label}
                    onClick={() => openApp(id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openApp(id); }
                    }}
                    className="gv-applnk"
                    style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 11, cursor: "pointer" }}
                  >
                    <div style={{ position: "relative" }}>
                      <AppIcon id={id} size={92} radius={22} filter="drop-shadow(0 12px 26px rgba(0,0,0,0.55))" />
                      {isOpen && (
                        <span style={{
                          position: "absolute", bottom: -6, left: "50%", transform: "translateX(-50%)",
                          width: 6, height: 6, borderRadius: "50%",
                          background: "#fff", boxShadow: "0 0 7px #fff",
                        }} />
                      )}
                    </div>
                    <span style={{ color: "#e6ffe7", fontSize: 13.5, fontWeight: 500, textAlign: "center" }}>
                      {a.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
