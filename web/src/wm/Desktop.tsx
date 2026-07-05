import React, { useRef, useEffect } from "react";
import { useWindows } from "../store/windows";
import { Waybar } from "./Waybar";
import { Launcher } from "./Launcher";
import { CommandBar } from "./CommandBar";
import { Window } from "./Window";
import { QuickPanel } from "./QuickPanel";

import bgServer from "../assets/bg_server.png";

const GV_CSS = `
*{box-sizing:border-box;}
html,body{margin:0;height:100%;background:#040804;overflow:hidden;}
@keyframes gv-blink{0%,100%{opacity:1;}50%{opacity:0;}}
@keyframes gv-pop{0%{transform:scale(0.92);opacity:0;}100%{transform:scale(1);opacity:1;}}
::-webkit-scrollbar{width:9px;height:9px;}
::-webkit-scrollbar-track{background:rgba(0,0,0,0.4);}
::-webkit-scrollbar-thumb{background:rgba(128,0,255,0.5);border-radius:4px;}
.gv-dockicon{transition:transform 0.16s cubic-bezier(0.25,1,0.5,1),box-shadow 0.16s;}
.gv-dockicon:hover{transform:translateY(-7px) scale(1.08);}
.gv-pill{transition:all 0.16s;}
.gv-pill:hover{background:rgba(0,255,0,0.12) !important;}
.gv-applnk{transition:transform 0.15s cubic-bezier(0.25,1,0.5,1);}
.gv-applnk:hover{transform:translateY(-4px) scale(1.07);}
@keyframes gv-fade{from{opacity:0;}to{opacity:1;}}
@keyframes gv-flicker{0%{opacity:0.85;}25%{opacity:1;}50%{opacity:0.8;}75%{opacity:1;}100%{opacity:0.9;}}
@property --gv-a{syntax:'<angle>';initial-value:0deg;inherits:false;}
@keyframes gv-spin{to{--gv-a:360deg;}}
.gv-win{border-radius:11px;box-shadow:0 12px 36px rgba(0,0,0,0.55);}
.gv-win-active{box-shadow:0 0 24px rgba(0,255,0,0.14),0 18px 48px rgba(0,0,0,0.6);}
.gv-win-active::before{content:'';position:absolute;inset:-2px;border-radius:13px;padding:2px;
  background:conic-gradient(from var(--gv-a),#ff0000,#ffff00,#00ff00,#00ffff,#0000ff,#ff00ff,#ff0000);
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;
  mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);mask-composite:exclude;
  animation:gv-spin 3.2s linear infinite;pointer-events:none;z-index:6;opacity:0.85;}
.gv-rng{-webkit-appearance:none;appearance:none;height:5px;border-radius:3px;background:rgba(0,255,0,0.18);outline:none;}
.gv-rng::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:#00ff00;box-shadow:0 0 8px #00ff00;cursor:pointer;}
.gv-rng::-moz-range-thumb{width:14px;height:14px;border:none;border-radius:50%;background:#00ff00;box-shadow:0 0 8px #00ff00;cursor:pointer;}
.gv-rez{transition:background 0.15s;}
.gv-rez:hover{background:rgba(0,255,0,0.18) !important;}
`;

export function Desktop() {
  const open = useWindows((s) => s.open);
  const panelOpen = useWindows((s) => s.panelOpen);
  const toggleCommandBar = useWindows((s) => s.toggleCommandBar);
  const desktopScale = useWindows((s) => s.desktopScale);
  const deskRef = useRef<HTMLDivElement>(null);

  // Global keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Cmd/Ctrl+K to toggle command bar. Match case-insensitively so Shift or
      // Caps Lock (which make e.key "K") don't swallow the shortcut.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        toggleCommandBar();
        return;
      }
    };

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggleCommandBar]);

  return (
    <>
      <style>{GV_CSS}</style>
      <div style={{
        position: "fixed", inset: 0,
        fontFamily: "'Space Grotesk', system-ui, -apple-system, sans-serif",
        background: `#040804 url('${bgServer}') center/cover`,
        overflow: "hidden",
        // User-adjustable desktop "resolution" — uniform UI zoom (QuickPanel slider).
        zoom: desktopScale,
      }}>
        {/* gradient overlays */}
        <div style={{ position: "absolute", inset: 0, background: "linear-gradient(rgba(2,8,4,0.66),rgba(1,4,8,0.74))" }} />
        <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 8999, background: "radial-gradient(ellipse at 50% 42%,transparent 58%,rgba(0,0,0,0.4) 100%)" }} />

        {/* waybar */}
        <Waybar />

        {/* quick settings panel */}
        {panelOpen && <QuickPanel />}

        {/* desktop area */}
        <div
          ref={deskRef}
          style={{ position: "absolute", top: 54, left: 0, right: 0, bottom: 84, zIndex: 10 }}
        >
          {/* empty state */}
          {open.length === 0 && (
            <div style={{
              position: "absolute", top: "50%", left: "50%", transform: "translate(-50%,-50%)",
              textAlign: "center", color: "rgba(204,255,204,0.5)",
            }}>
              <div style={{
                fontFamily: "'Space Grotesk',system-ui,sans-serif", fontWeight: 700,
                fontSize: 30, color: "#00f3ff",
                textShadow: "0 0 12px rgba(0,243,255,0.4)", letterSpacing: 2,
              }}>AGENT VAULT</div>
              <div style={{ fontSize: 13, marginTop: 8 }}>press ◴ APPS in the bar to open an application</div>
            </div>
          )}

          {/* windows */}
          {open.map((id) => (
            <Window key={id} app={id} deskRef={deskRef as React.RefObject<HTMLDivElement>} />
          ))}

          {/* launcher overlay */}
          <Launcher />

          {/* command bar overlay */}
          <CommandBar />
        </div>
      </div>
    </>
  );
}
