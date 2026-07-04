import React, { useEffect, useRef, useState } from "react";
import { useWindows } from "../store/windows";
import { APPS, type AppId } from "./apps";
import { clampResize, snapFor } from "./geometry";
import { ScreenRouter } from "../screens/ScreenRouter";
import { ErrorBoundary } from "../ui/ErrorBoundary";

interface WindowProps {
  app: AppId;
  deskRef: React.RefObject<HTMLDivElement>;
}

// Keep at least this many px of a window visible when dragging it toward a
// horizontal edge, so it can always be grabbed back.
const EDGE_MARGIN = 40;

export function Window({ app, deskRef }: WindowProps) {
  // Per-window selectors — subscribe only to THIS window's slice so dragging
  // one window does not re-render every other window.
  const p = useWindows((s) => s.pos[app]) ?? { x: 60, y: 40 };
  const rawSize = useWindows((s) => s.size[app]);
  const zVal = useWindows((s) => s.z[app] ?? 1);
  const zTop = useWindows((s) => s.zTop);
  const isMinimized = useWindows((s) => !!s.minimized[app]);
  const focus = useWindows((s) => s.focus);
  const close = useWindows((s) => s.close);
  const minimize = useWindows((s) => s.minimize);
  const setPos = useWindows((s) => s.setPos);
  const setSize = useWindows((s) => s.setSize);

  const s = rawSize ?? { w: APPS[app].w, h: APPS[app].h };
  const isTop = zVal === zTop;

  // drag state (refs to avoid re-renders during mousemove)
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);
  const resizeRef = useRef<{ dir: string; sx: number; sy: number; w0: number; h0: number; x0: number; y0: number } | null>(null);
  const restoredRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const [snapping, setSnapping] = useState(false);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      // Desktop.tsx applies `zoom: desktopScale` — pointer coordinates are in
      // visual px while window pos/size are in layout px, so divide by scale.
      const scale = useWindows.getState().desktopScale || 1;
      if (dragRef.current) {
        const { dx, dy } = dragRef.current;
        const rect = deskRef.current?.getBoundingClientRect() ?? { left: 0, top: 0 };
        const st = useWindows.getState();
        const w = st.size[app]?.w ?? APPS[app].w;
        const deskW = deskRef.current?.clientWidth ?? 1280;
        let x = (e.clientX - rect.left) / scale - dx;
        // Clamp so at least EDGE_MARGIN px stay visible on each side.
        x = Math.max(-(w - EDGE_MARGIN), Math.min(x, deskW - EDGE_MARGIN));
        const y = Math.max(0, (e.clientY - rect.top) / scale - dy);
        setPos(app, x, y);
      } else if (resizeRef.current) {
        const { dir, sx, sy, w0, h0, x0, y0 } = resizeRef.current;
        const ddx = (e.clientX - sx) / scale;
        const ddy = (e.clientY - sy) / scale;
        const result = clampResize(dir, ddx, ddy, w0, h0, x0, y0);
        setPos(app, result.x, result.y);
        setSize(app, result.w, result.h);
      }
    };

    const onUp = () => {
      dragRef.current = null;
      resizeRef.current = null;
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [app, deskRef, setPos, setSize]);

  const onDragStart = (e: React.MouseEvent) => {
    focus(app);
    const scale = useWindows.getState().desktopScale || 1;
    const rect = deskRef.current?.getBoundingClientRect() ?? { left: 0, top: 0 };
    dragRef.current = {
      dx: (e.clientX - rect.left) / scale - p.x,
      dy: (e.clientY - rect.top) / scale - p.y,
    };
    e.preventDefault();
  };

  const onResizeStart = (dir: string) => (e: React.MouseEvent) => {
    focus(app);
    resizeRef.current = { dir, sx: e.clientX, sy: e.clientY, w0: s.w, h0: s.h, x0: p.x, y0: p.y };
    e.stopPropagation();
    e.preventDefault();
  };

  const onMax = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const rect = deskRef.current?.getBoundingClientRect() ?? { left: 0, top: 0, width: 1280, height: 720 };
    if (restoredRef.current) {
      const saved = restoredRef.current;
      restoredRef.current = null;
      setSnapping(true);
      setPos(app, saved.x, saved.y);
      setSize(app, saved.w, saved.h);
      setTimeout(() => setSnapping(false), 240);
    } else {
      restoredRef.current = { x: p.x, y: p.y, w: s.w, h: s.h };
      setSnapping(true);
      const snapped = snapFor(rect.width / 2, 0, rect.width, rect.height);
      if (snapped) { setPos(app, snapped.x, snapped.y); setSize(app, snapped.w, snapped.h); }
      setTimeout(() => setSnapping(false), 240);
    }
    focus(app);
  };

  const onClose = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    close(app);
  };

  const onMinimize = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    minimize(app);
  };

  const a = APPS[app];
  const borderColor = isTop ? "rgba(0,255,0,0.5)" : "rgba(0,255,0,0.18)";

  // Minimized windows stay mounted in the store but render nothing; the
  // Waybar task button restores them via focus().
  if (isMinimized) return null;

  return (
    <div
      className={`gv-win${isTop ? " gv-win-active" : ""}`}
      onMouseDown={() => focus(app)}
      style={{
        position: "absolute",
        left: p.x,
        top: p.y,
        width: s.w,
        height: s.h,
        zIndex: zVal,
        ...(snapping ? {
          transition: "left .22s cubic-bezier(.25,1,.5,1), top .22s cubic-bezier(.25,1,.5,1), width .22s cubic-bezier(.25,1,.5,1), height .22s cubic-bezier(.25,1,.5,1)",
        } : {}),
      }}
    >
      <div style={{
        position: "absolute", inset: 0, borderRadius: 11, overflow: "hidden",
        border: `1px solid ${borderColor}`,
        background: "rgba(8,12,16,0.52)",
        backdropFilter: "blur(16px) saturate(135%)",
        WebkitBackdropFilter: "blur(16px) saturate(135%)",
        display: "flex", flexDirection: "column",
      }}>
        {/* title bar */}
        <div
          onMouseDown={onDragStart}
          onDoubleClick={onMax}
          style={{
            flex: "none", display: "flex", alignItems: "center", gap: 9,
            padding: "8px 13px",
            background: "linear-gradient(rgba(255,255,255,0.07),rgba(255,255,255,0.02))",
            borderBottom: "1px solid rgba(0,255,0,0.25)",
            cursor: "grab", userSelect: "none",
          }}
        >
          {/* traffic lights */}
          <span
            role="button"
            tabIndex={0}
            aria-label={`Close ${a.title}`}
            onMouseDown={(e) => { e.stopPropagation(); }}
            onClick={onClose}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClose(e); }}
            style={{ width: 12, height: 12, borderRadius: "50%", background: "#ff5f56", boxShadow: "0 0 5px #ff5f56", cursor: "pointer", flexShrink: 0 }}
          />
          <span
            role="button"
            tabIndex={0}
            aria-label={`Minimize ${a.title}`}
            title="minimize"
            onMouseDown={(e) => { e.stopPropagation(); }}
            onClick={onMinimize}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onMinimize(e); }}
            style={{ width: 12, height: 12, borderRadius: "50%", background: "#ffbd2e", boxShadow: "0 0 5px #ffbd2e", cursor: "pointer", flexShrink: 0 }}
          />
          <span
            role="button"
            tabIndex={0}
            aria-label="Maximize or restore window"
            onMouseDown={(e) => { e.stopPropagation(); }}
            onClick={onMax}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onMax(e); }}
            title="maximize / restore"
            style={{ width: 12, height: 12, borderRadius: "50%", background: "#27c93f", boxShadow: "0 0 5px #27c93f", cursor: "pointer", flexShrink: 0 }}
          />
          <span style={{ marginLeft: 8, color: "#888", fontSize: 13, fontFamily: "'Space Grotesk',system-ui,sans-serif", fontWeight: 700, letterSpacing: -0.5 }}>
            {a.glyph}
          </span>
          <span style={{ marginLeft: "auto", color: "#00f3ff", fontSize: 12, letterSpacing: 1, textShadow: "0 0 5px #00f3ff" }}>
            {a.title}
          </span>
        </div>

        {/* content area */}
        <div style={{ flex: 1, position: "relative", overflow: "hidden", background: "rgba(6,10,14,0.16)" }}>
          <div style={{ position: "absolute", inset: 0 }}>
            <ErrorBoundary resetKey={app}>
              <ScreenRouter app={app} />
            </ErrorBoundary>
          </div>
        </div>
      </div>

      {/* resize handles */}
      <span onMouseDown={onResizeStart("n")} className="gv-rez" style={{ position: "absolute", top: -4, left: 24, right: 24, height: 10, cursor: "ns-resize", zIndex: 20, borderRadius: 6 }} />
      <span onMouseDown={onResizeStart("s")} className="gv-rez" style={{ position: "absolute", bottom: -4, left: 24, right: 24, height: 10, cursor: "ns-resize", zIndex: 20, borderRadius: 6 }} />
      <span onMouseDown={onResizeStart("e")} className="gv-rez" style={{ position: "absolute", right: -4, top: 42, bottom: 24, width: 10, cursor: "ew-resize", zIndex: 20, borderRadius: 6 }} />
      <span onMouseDown={onResizeStart("w")} className="gv-rez" style={{ position: "absolute", left: -4, top: 42, bottom: 24, width: 10, cursor: "ew-resize", zIndex: 20, borderRadius: 6 }} />
      <span onMouseDown={onResizeStart("nw")} style={{ position: "absolute", left: -5, top: -5, width: 16, height: 16, cursor: "nwse-resize", zIndex: 21 }} />
      <span onMouseDown={onResizeStart("ne")} style={{ position: "absolute", right: -5, top: -5, width: 16, height: 16, cursor: "nesw-resize", zIndex: 21 }} />
      <span onMouseDown={onResizeStart("sw")} style={{ position: "absolute", left: -5, bottom: -5, width: 16, height: 16, cursor: "nesw-resize", zIndex: 21 }} />
      <span onMouseDown={onResizeStart("se")} style={{ position: "absolute", right: -4, bottom: -4, width: 18, height: 18, cursor: "nwse-resize", zIndex: 22 }} />
    </div>
  );
}
