// web/src/ui/Ns.tsx
import React, { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { C, FONT_MONO, FONT_UI, useTheme } from "../theme";

// Helper to get CSS var with fallback
function cv(color: string, fallback: string): string {
  return `var(--ns-${color}, ${fallback})`;
}

// ── NsTitle ───────────────────────────────────────────────
export function NsTitle({ children, sub }: { children: ReactNode; sub?: string }) {
  const { C } = useTheme();
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        fontFamily: FONT_UI, fontSize: 18, fontWeight: 700, color: cv("cyan", C.cyan),
        borderBottom: `2px solid ${cv("purple", C.purple)}`, paddingBottom: 4, letterSpacing: 0.5,
      }}>
        {children}
      </div>
      {sub && (
        <div style={{ color: cv("dim", C.dim), fontSize: 12, marginTop: 6, fontFamily: FONT_MONO }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ── NsCard ────────────────────────────────────────────────
export function NsCard({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  const { C } = useTheme();
  return (
    <div style={{
      background: "rgba(255,255,255,0.022)",
      border: "1px solid rgba(255,255,255,0.09)",
      borderRadius: 11,
      overflow: "hidden",
      ...style,
    }}>
      {children}
    </div>
  );
}

// ── NsStat ────────────────────────────────────────────────
export function NsStat({ label, value, color }: { label: string; value: ReactNode; color?: string }) {
  const { C } = useTheme();
  return (
    <div style={{
      border: `1px solid ${cv("green", C.green)}`,
      background: "rgba(0,255,0,0.04)",
      borderRadius: 8,
      padding: "12px 16px",
      minWidth: 100,
    }}>
      <div style={{ color: cv("muted-green", C.mutedGreen), fontSize: 10, fontFamily: FONT_UI, letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{
        color: color ?? cv("green", C.green), fontSize: 26, fontWeight: 700,
        letterSpacing: -0.5, marginTop: 2,
      }}>
        {value}
      </div>
    </div>
  );
}

// ── NsButton ──────────────────────────────────────────────
const BTN_COLORS: Record<string, { border: string; color: string; glow: string }> = {
  cyan:  { border: C.cyan,  color: C.cyan,  glow: "rgba(0,243,255,0.35)"  },
  green: { border: C.green, color: C.green, glow: "rgba(0,255,0,0.35)"    },
  red:   { border: C.red,   color: C.red,   glow: "rgba(255,95,86,0.35)"  },
};

export function NsButton({
  children, variant = "cyan", onClick, disabled,
}: {
  children: ReactNode; variant?: "cyan" | "green" | "red";
  onClick?: () => void; disabled?: boolean;
}) {
  const { C } = useTheme();
  const col = BTN_COLORS[variant];
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: "transparent",
        border: `1px solid ${cv(variant, col.border)}`,
        color: cv(variant, col.color),
        fontFamily: FONT_UI,
        fontSize: 12,
        fontWeight: 600,
        padding: "5px 14px",
        borderRadius: 6,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
        transition: "all 0.16s cubic-bezier(0.25,1,0.5,1)",
      }}
      onMouseEnter={(e) => {
        if (disabled) return;
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = cv(variant, col.color);
        el.style.color = cv("ground", C.ground);
        el.style.boxShadow = `0 0 10px ${col.glow}`;
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLButtonElement;
        el.style.background = "transparent";
        el.style.color = cv(variant, col.color);
        el.style.boxShadow = "none";
      }}
    >
      {children}
    </button>
  );
}

// ── NsToggle ──────────────────────────────────────────────
export function NsToggle({
  on, onChange, label,
}: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  const { C } = useTheme();
  const tBorder = on ? cv("bright", C.bright) : "rgba(0,255,0,0.3)";
  const tBg     = on ? "rgba(39,201,63,0.25)" : "rgba(0,0,0,0.4)";
  const knobX   = on ? 19 : 2;
  const knobC   = on ? cv("bright", C.bright) : cv("dim2", C.dim2);

  function handleKey(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChange(!on);
    }
  }

  return (
    <span
      role="switch"
      aria-checked={on}
      aria-label={label}
      tabIndex={0}
      onClick={() => onChange(!on)}
      onKeyDown={handleKey}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLSpanElement).style.boxShadow = `0 0 6px rgba(0,255,0,0.25)`;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLSpanElement).style.boxShadow = "none";
      }}
      style={{
        display: "inline-block", cursor: "pointer",
        width: 38, height: 20, borderRadius: 11,
        border: `1px solid ${tBorder}`, background: tBg,
        position: "relative", flexShrink: 0,
      }}
    >
      <span style={{
        position: "absolute", top: 1, left: knobX,
        width: 16, height: 16, borderRadius: "50%",
        background: knobC, transition: "all 0.18s",
      }} />
    </span>
  );
}

// ── NsChip ────────────────────────────────────────────────
export function NsChip({
  children, active, onClick,
}: { children: ReactNode; active?: boolean; onClick?: () => void }) {
  const { C } = useTheme();
  return (
    <span
      onClick={onClick}
      onMouseEnter={(e) => {
        if (!onClick) return;
        const el = e.currentTarget as HTMLSpanElement;
        el.style.background = cv("cyan", C.cyan);
        el.style.color = cv("ground", C.ground);
        el.style.boxShadow = `0 0 8px rgba(0,243,255,0.4)`;
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLSpanElement;
        el.style.background = active ? "rgba(0,243,255,0.12)" : "transparent";
        el.style.color = active ? cv("cyan", C.cyan) : cv("dim", C.dim);
        el.style.boxShadow = "none";
      }}
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        border: `1px solid ${active ? cv("cyan", C.cyan) : "rgba(0,243,255,0.3)"}`,
        background: active ? "rgba(0,243,255,0.12)" : "transparent",
        color: active ? cv("cyan", C.cyan) : cv("dim", C.dim),
        fontSize: 11,
        fontFamily: FONT_UI,
        cursor: onClick ? "pointer" : "default",
        transition: "all 0.15s",
      }}
    >
      {children}
    </span>
  );
}

// ── NsInput ───────────────────────────────────────────────
export function NsInput({
  value, onChange, placeholder, mono, id, "aria-label": ariaLabel, type,
}: {
  value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean;
  id?: string; "aria-label"?: string; type?: string;
}) {
  const { C } = useTheme();
  return (
    <input
      id={id}
      type={type ?? "text"}
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        background: "rgba(0,0,0,0.45)",
        border: `1px solid rgba(0,255,0,0.3)`,
        color: cv("text", C.text),
        fontFamily: mono ? FONT_MONO : FONT_UI,
        fontSize: 12,
        padding: "5px 10px",
        borderRadius: 6,
        outline: "none",
        width: "100%",
      }}
    />
  );
}

// ── NsCapacityBar ─────────────────────────────────────────
export function NsCapacityBar({ pct }: { pct: number }) {
  const { C } = useTheme();
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <span style={{
      display: "block", height: 12, borderRadius: 5,
      background: "rgba(0,0,0,0.5)",
      border: "1px solid rgba(0,255,0,0.2)",
      position: "relative", overflow: "hidden",
    }}>
      <span
        data-testid="ns-cap-fill"
        style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${clamped}%`,
          background: `linear-gradient(90deg,${cv("bright", C.bright)},${cv("cyan", C.cyan)})`,
          boxShadow: "0 0 10px rgba(0,243,255,0.4)",
        }}
      />
    </span>
  );
}
