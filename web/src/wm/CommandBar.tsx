import React, { useEffect, useRef, useState } from "react";
import { useWindows } from "../store/windows";
import { APPS, APP_ORDER, type AppId } from "./apps";

interface CommandAction {
  id: string;
  label: string;
  type: "app" | "theme" | "assistant";
  appId?: AppId;
}

export function CommandBar() {
  const commandBarOpen = useWindows((s) => s.commandBarOpen);
  const open = useWindows((s) => s.open);
  const openApp = useWindows((s) => s.openApp);
  const toggleCommandBar = useWindows((s) => s.toggleCommandBar);
  const setCommandBarOpen = useWindows((s) => s.setCommandBarOpen);
  const theme = useWindows((s) => s.theme);
  const setTheme = useWindows((s) => s.setTheme);

  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Build the action index
  const buildActions = (): CommandAction[] => {
    const actions: CommandAction[] = [];

    // Add app actions
    for (const id of APP_ORDER) {
      const app = APPS[id];
      actions.push({
        id: `app-${id}`,
        label: `${app.glyph} ${app.label}`,
        type: "app",
        appId: id,
      });
    }

    // Add global actions
    actions.push({
      id: "assistant",
      label: "✦ Open AI Assistant",
      type: "assistant",
    });

    actions.push({
      id: "theme",
      label: `Toggle theme (${theme === "dark" ? "Light" : "Dark"} mode)`,
      type: "theme",
    });

    return actions;
  };

  const allActions = buildActions();

  // Filter actions based on query
  const filteredActions = query.trim() === ""
    ? allActions
    : allActions.filter(action =>
        action.label.toLowerCase().includes(query.toLowerCase())
      );

  // Reset selected index when filtered list changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Focus input when opened
  useEffect(() => {
    if (commandBarOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [commandBarOpen]);

  // Global keyboard handling
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!commandBarOpen) return;

      switch (e.key) {
        case "Escape":
          e.preventDefault();
          toggleCommandBar();
          break;
        case "ArrowDown":
          e.preventDefault();
          setSelectedIndex(prev =>
            prev < filteredActions.length - 1 ? prev + 1 : prev
          );
          break;
        case "ArrowUp":
          e.preventDefault();
          setSelectedIndex(prev => (prev > 0 ? prev - 1 : 0));
          break;
        case "Enter":
          e.preventDefault();
          if (filteredActions.length > 0) {
            executeAction(filteredActions[selectedIndex]);
          }
          break;
      }
    };

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [commandBarOpen, filteredActions, selectedIndex]);

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current && selectedIndex >= 0) {
      const selectedElement = listRef.current.children[selectedIndex] as HTMLElement;
      if (selectedElement && typeof selectedElement.scrollIntoView === "function") {
        selectedElement.scrollIntoView({ block: "nearest" });
      }
    }
  }, [selectedIndex]);

  const executeAction = (action: CommandAction) => {
    switch (action.type) {
      case "app":
        if (action.appId) {
          openApp(action.appId);
        }
        break;
      case "assistant":
        openApp("command");
        break;
      case "theme":
        setTheme(theme === "dark" ? "light" : "dark");
        break;
    }
    setCommandBarOpen(false);
    setQuery("");
  };

  if (!commandBarOpen) return null;

  return (
    <div
      onClick={() => setCommandBarOpen(false)}
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 7900,
        background: "rgba(2, 5, 4, 0.34)",
        backdropFilter: "blur(22px) saturate(115%)",
        WebkitBackdropFilter: "blur(22px) saturate(115%)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        paddingTop: 120,
      }}
    >
      {/* Command bar container */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 560,
          maxWidth: "90%",
          background: "rgba(255, 255, 255, 0.06)",
          border: "1px solid rgba(255, 255, 255, 0.12)",
          borderRadius: 14,
          boxShadow: "0 12px 40px rgba(0, 0, 0, 0.4)",
          overflow: "hidden",
        }}
      >
        {/* Input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "14px 18px",
            borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
          }}
        >
          <span style={{ color: "#19e3d0", fontSize: 18 }}>⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command or search..."
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "#eafff0",
              fontFamily: "'Space Grotesk', system-ui, sans-serif",
              fontSize: 16,
            }}
          />
          <span
            style={{
              color: "#8aa",
              fontSize: 11,
              border: "1px solid rgba(255, 255, 255, 0.14)",
              borderRadius: 6,
              padding: "2px 8px",
            }}
          >
            esc
          </span>
        </div>

        {/* Results list */}
        <div
          ref={listRef}
          style={{
            maxHeight: "320px",
            overflowY: "auto",
            padding: "8px",
          }}
        >
          {filteredActions.length === 0 ? (
            <div
              style={{
                padding: "20px",
                textAlign: "center",
                color: "#8aa",
                fontSize: 14,
              }}
            >
              No results found
            </div>
          ) : (
            filteredActions.map((action, index) => {
              const isSelected = index === selectedIndex;
              const isOpen = action.type === "app" && action.appId && Array.isArray(open) && open.includes(action.appId);

              return (
                <div
                  key={action.id}
                  role="button"
                  tabIndex={0}
                  aria-label={action.label}
                  onClick={() => executeAction(action)}
                  onMouseEnter={() => setSelectedIndex(index)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      executeAction(action);
                    }
                  }}
                  style={{
                    padding: "10px 14px",
                    borderRadius: 8,
                    background: isSelected
                      ? "rgba(0, 243, 255, 0.12)"
                      : "transparent",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    color: isSelected ? "#00f3ff" : "#e6ffe7",
                    fontSize: 14,
                    transition: "background 0.1s",
                  }}
                >
                  <span style={{ flex: 1 }}>{action.label}</span>
                  {isOpen && (
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: "#00ff00",
                        boxShadow: "0 0 6px #00ff00",
                      }}
                    />
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer with hints */}
        <div
          style={{
            padding: "8px 14px",
            borderTop: "1px solid rgba(255, 255, 255, 0.08)",
            display: "flex",
            gap: 16,
            fontSize: 11,
            color: "#8aa",
          }}
        >
          <span>↑↓ navigate</span>
          <span>enter select</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}