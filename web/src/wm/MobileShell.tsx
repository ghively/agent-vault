import React, { useState } from "react";
import { useWindows } from "../store/windows";
import { APP_ORDER, APPS, type AppId } from "./apps";
import { ScreenRouter } from "../screens/ScreenRouter";
import { C, FONT_UI } from "../theme";

import bgServer from "../assets/bg_server.png";

const GV_CSS = `
*{box-sizing:border-box;}
html,body{margin:0;height:100%;background:#040804;overflow:hidden;}
@keyframes gv-blink{0%,100%{opacity:1;}50%{opacity:0;}}
@keyframes gv-pop{0%{transform:scale(0.92);opacity:0;}100%{transform:scale(1);opacity:1;}}
::-webkit-scrollbar{width:9px;height:9px;}
::-webkit-scrollbar-track{background:rgba(0,0,0,0.4);}
::-webkit-scrollbar-thumb{background:rgba(128,0,255,0.5);border-radius:4px;}
.gv-applnk{transition:transform 0.15s cubic-bezier(0.25,1,0.5,1);}
.gv-applnk:hover{transform:translateY(-4px) scale(1.07);}
@keyframes gv-fade{from{opacity:0;}to{opacity:1;}}
.gv-pill{transition:all 0.16s;}
.gv-pill:hover{background:rgba(0,255,0,0.12) !important;}
`;

const MOBILE_HEADER_HEIGHT = 56;

export function MobileShell() {
  const open = useWindows((s) => s.open);
  const openApp = useWindows((s) => s.openApp);
  const close = useWindows((s) => s.close);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Mobile shows only the focused (highest z-index) app, or the first if none focused
  const focusedApp = React.useMemo(() => {
    if (open.length === 0) return null;
    // Simply return the last opened app (most recent)
    return open[open.length - 1];
  }, [open]);

  const handleAppClick = (appId: AppId) => {
    openApp(appId);
    setDrawerOpen(false);
  };

  return (
    <>
      <style>{GV_CSS}</style>
      <div style={{
        position: "fixed",
        inset: 0,
        fontFamily: FONT_UI,
        background: `#040804 url('${bgServer}') center/cover`,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}>
        {/* Top bar with hamburger menu */}
        <div style={{
          height: MOBILE_HEADER_HEIGHT,
          background: "rgba(4, 8, 4, 0.95)",
          borderBottom: `1px solid ${C.green}`,
          display: "flex",
          alignItems: "center",
          padding: "0 16px",
          zIndex: 1000,
        }}>
          <button
            onClick={() => setDrawerOpen(!drawerOpen)}
            style={{
              background: "transparent",
              border: "none",
              color: C.green,
              fontSize: 24,
              cursor: "pointer",
              padding: 8,
              marginRight: 16,
            }}
            aria-label="Toggle app drawer"
          >
            {drawerOpen ? "✕" : "☰"}
          </button>
          <div style={{
            color: C.cyan,
            fontSize: 18,
            fontWeight: 600,
            textOverflow: "ellipsis",
            overflow: "hidden",
            whiteSpace: "nowrap",
          }}>
            {focusedApp ? APPS[focusedApp]?.title ?? "SynapseNAS" : "SynapseNAS"}
          </div>
          {focusedApp && (
            <button
              onClick={() => close(focusedApp)}
              style={{
                marginLeft: "auto",
                background: "transparent",
                border: `1px solid ${C.red}`,
                color: C.red,
                borderRadius: 4,
                padding: "4px 12px",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Close
            </button>
          )}
        </div>

        {/* App drawer overlay */}
        {drawerOpen && (
          <>
            <div
              onClick={() => setDrawerOpen(false)}
              style={{
                position: "fixed",
                inset: 0,
                background: "rgba(0, 0, 0, 0.5)",
                zIndex: 900,
              }}
            />
            <div style={{
              position: "fixed",
              top: MOBILE_HEADER_HEIGHT,
              left: 0,
              right: 0,
              bottom: 0,
              background: "rgba(4, 8, 4, 0.98)",
              zIndex: 901,
              overflowY: "auto",
              padding: 16,
            }}>
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))",
                gap: 12,
              }}>
                {APP_ORDER.map((appId) => {
                  const app = APPS[appId];
                  if (!app) return null;
                  return (
                    <button
                      key={appId}
                      onClick={() => handleAppClick(appId)}
                      className="gv-applnk gv-pill"
                      style={{
                        background: `linear-gradient(145deg, ${app.color}22, ${app.color}11)`,
                        border: `1px solid ${app.color}66`,
                        borderRadius: 8,
                        padding: 12,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        gap: 8,
                        cursor: "pointer",
                        color: C.text,
                        textAlign: "center",
                      }}
                    >
                      <div style={{ fontSize: 32 }}>{app.glyph}</div>
                      <div style={{ fontSize: 11, fontWeight: 600, lineHeight: 1.2 }}>
                        {app.label}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {/* Main content area - shows focused app or empty state */}
        <div style={{
          flex: 1,
          position: "relative",
          overflow: "auto",
        }}>
          {focusedApp ? (
            <div style={{
              position: "absolute",
              inset: 0,
              background: "rgba(4, 8, 4, 0.92)",
            }}>
              <ScreenRouter app={focusedApp} />
            </div>
          ) : (
            <div style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              textAlign: "center",
              color: "rgba(204,255,204,0.5)",
            }}>
              <div style={{
                fontFamily: FONT_UI,
                fontWeight: 700,
                fontSize: 24,
                color: C.cyan,
                textShadow: "0 0 12px rgba(0,243,255,0.4)",
                letterSpacing: 2,
                marginBottom: 8,
              }}>
                AGENT VAULT
              </div>
              <div style={{ fontSize: 13 }}>
                Tap ☰ to open an application
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
