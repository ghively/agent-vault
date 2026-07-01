export const FONT_UI = "'Space Grotesk', system-ui, -apple-system, sans-serif";
export const FONT_MONO = "'JetBrains Mono', monospace";

// PHOSPHOR canonical tokens. Values are CSS-variable references so EVERY
// consumer (1,271+ `C.*` references across screens) becomes theme-aware in one
// place: dark theme is pixel-identical (the :root values in theme.css equal the
// prior literals), and [data-theme="light"] overrides now reach screen bodies.
// useTheme()/CFromVars below still resolve the computed hex for components that
// want a concrete value.
export const C = {
  ground:     "var(--ns-ground)",
  green:      "var(--ns-green)",
  bright:     "var(--ns-bright)",
  cyan:       "var(--ns-cyan)",
  purple:     "var(--ns-purple)",
  lilac:      "var(--ns-lilac)",
  amber:      "var(--ns-amber)",
  red:        "var(--ns-red)",
  blue:       "var(--ns-blue)",
  text:       "var(--ns-text)",
  mutedGreen: "var(--ns-muted-green)",
  dim:        "var(--ns-dim)",
  dim2:       "var(--ns-dim2)",
  // backward-compat aliases (existing screens import these)
  bg:         "var(--ns-ground)",   // alias → ground
  greenSoft:  "var(--ns-bright)",   // alias → bright
} as const;

// Theme types
export type Theme = "dark" | "light";
export type Density = "comfortable" | "compact";

// Helper to get computed CSS variable value with fallback
function getCssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const root = document.documentElement;
  const value = getComputedStyle(root).getPropertyValue(`--ns-${name}`);
  return value?.trim() || fallback;
}

// useTheme hook — provides C + spacing utilities (simplified for vault, no dynamic theme switching)
export function useTheme() {
  // Build C object from CSS variables (falls back to dark literals for pixel-perfect dark)
  const CFromVars: Record<string, string> = {
    ground: getCssVar("ground", C.ground),
    green: getCssVar("green", C.green),
    bright: getCssVar("bright", C.bright),
    cyan: getCssVar("cyan", C.cyan),
    purple: getCssVar("purple", C.purple),
    lilac: getCssVar("lilac", C.lilac),
    amber: getCssVar("amber", C.amber),
    red: getCssVar("red", C.red),
    blue: getCssVar("blue", C.blue),
    text: getCssVar("text", C.text),
    mutedGreen: getCssVar("muted-green", C.mutedGreen),
    dim: getCssVar("dim", C.dim),
    dim2: getCssVar("dim2", C.dim2),
    bg: getCssVar("ground", C.bg),
    greenSoft: getCssVar("bright", C.greenSoft),
  };

  // Spacing helper — multiplies base pad (8px) by density scale
  const pad = (n: number): number => {
    const scale = parseFloat(getCssVar("pad", "1"));
    return 8 * n * scale;
  };

  return { C: CFromVars, density: "comfortable" as Density, pad, theme: "dark" as Theme };
}
