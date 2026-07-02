// web/src/wm/TabbedApp.tsx
// PHOSPHOR window-manager tabbed shell.
//
// Renders a tab bar at the top of a window content area and delegates the
// active screen to a caller-supplied renderTab function (typically ScreenRouter).
// This avoids a circular import: TabbedApp never imports ScreenRouter directly.

import React, { useState } from "react";
import type { AppId } from "./apps";
import type { TabDef } from "./consolidation";
import { C, FONT_UI } from "../theme";

export interface TabbedAppProps {
  /** Ordered list of tabs; first tab is active by default. */
  tabs: TabDef[];
  /** Render the screen for the given AppId (caller supplies, e.g. ScreenRouter). */
  renderTab: (id: AppId) => React.ReactNode;
}

export function TabbedApp({ tabs, renderTab }: TabbedAppProps) {
  const [activeId, setActiveId] = useState<AppId>(tabs[0]?.id ?? ("" as AppId));

  if (tabs.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Tab bar */}
      <div
        role="tablist"
        aria-label="App sections"
        style={{
          display: "flex",
          flexShrink: 0,
          gap: 2,
          padding: "0 12px",
          background: "rgba(0,0,0,0.28)",
          borderBottom: `1px solid rgba(0,255,0,0.2)`,
          overflowX: "auto",
        }}
      >
        {tabs.map((tab) => {
          const isActive = tab.id === activeId;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              aria-controls={`tabpanel-${tab.id}`}
              tabIndex={isActive ? 0 : -1}
              onClick={() => setActiveId(tab.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setActiveId(tab.id);
                }
                // Arrow key navigation within the tab list
                if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                  e.preventDefault();
                  const idx = tabs.findIndex((t) => t.id === activeId);
                  const next =
                    e.key === "ArrowRight"
                      ? (idx + 1) % tabs.length
                      : (idx - 1 + tabs.length) % tabs.length;
                  setActiveId(tabs[next].id);
                }
              }}
              style={{
                padding: "7px 16px",
                fontFamily: FONT_UI,
                fontSize: 12.5,
                fontWeight: isActive ? 700 : 400,
                color: isActive ? C.cyan : C.dim,
                background: "transparent",
                border: "none",
                borderBottom: isActive
                  ? `2px solid ${C.cyan}`
                  : "2px solid transparent",
                cursor: "pointer",
                outline: "none",
                transition: "color 0.15s, border-bottom-color 0.15s",
                whiteSpace: "nowrap",
                letterSpacing: 0.3,
                textShadow: isActive ? `0 0 8px ${C.cyan}80` : "none",
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Active screen */}
      <div
        id={`tabpanel-${activeId}`}
        role="tabpanel"
        aria-label={tabs.find((t) => t.id === activeId)?.label}
        style={{ flex: 1, overflow: "hidden", position: "relative" }}
      >
        <div style={{ position: "absolute", inset: 0, overflow: "auto" }}>
          {renderTab(activeId)}
        </div>
      </div>
    </div>
  );
}
