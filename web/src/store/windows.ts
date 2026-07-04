import { create } from "zustand";
import { APPS, type AppId } from "../wm/apps";

interface XY { x: number; y: number }
interface WH { w: number; h: number }

interface WinState {
  open: AppId[];
  z: Partial<Record<AppId, number>>;
  zTop: number;
  pos: Partial<Record<AppId, XY>>;
  size: Partial<Record<AppId, WH>>;
  minimized: Partial<Record<AppId, boolean>>;
  detailSlug: string;
  launcherOpen: boolean;
  commandBarOpen: boolean;
  panelOpen: boolean;
  launcherQuery: string;
  desktopScale: number;
  theme: "dark" | "light";
  density: "comfortable" | "compact";
  setDesktopScale: (scale: number) => void;
  setTheme: (theme: "dark" | "light") => void;
  setDensity: (density: "comfortable" | "compact") => void;
  openApp: (id: AppId, slug?: string) => void;
  focus: (id: AppId) => void;
  close: (id: AppId) => void;
  minimize: (id: AppId) => void;
  setPos: (id: AppId, x: number, y: number) => void;
  setSize: (id: AppId, w: number, h: number) => void;
  setDetailSlug: (slug: string) => void;
  toggleLauncher: () => void;
  toggleCommandBar: () => void;
  setCommandBarOpen: (open: boolean) => void;
  togglePanel: () => void;
  setLauncherQuery: (q: string) => void;
  reset: () => void;
}

const DEFAULT_POS: Partial<Record<AppId, XY>> = {
  vault: { x: 60, y: 40 },
};

// Desktop scale ("resolution") — user-adjustable UI zoom, persisted across sessions.
export const SCALE_MIN = 0.7;
export const SCALE_MAX = 1.4;
const SCALE_KEY = "vault.desktopScale";
const THEME_KEY = "vault.theme";
const DENSITY_KEY = "vault.density";
const clampScale = (s: number) => Math.min(SCALE_MAX, Math.max(SCALE_MIN, s));

function loadScale(): number {
  try {
    const raw = localStorage.getItem(SCALE_KEY);
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) ? clampScale(n) : 1;
  } catch {
    return 1;
  }
}

function loadTheme(): "dark" | "light" {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return raw === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function loadDensity(): "comfortable" | "compact" {
  try {
    const raw = localStorage.getItem(DENSITY_KEY);
    return raw === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

// theme.css keys its variable overrides off [data-theme="light"] and
// [data-density="compact"] — stamp both on <html> so they actually apply.
function applyPrefsToDom(theme: "dark" | "light", density: "comfortable" | "compact") {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.density = density;
}

const initial = () => ({
  open: ["vault"] as AppId[],
  z: { vault: 1 } as Partial<Record<AppId, number>>,
  zTop: 1,
  pos: { ...DEFAULT_POS },
  size: {} as Partial<Record<AppId, WH>>,
  minimized: {} as Partial<Record<AppId, boolean>>,
  detailSlug: "",
  launcherOpen: false,
  commandBarOpen: false,
  panelOpen: false,
  launcherQuery: "",
  desktopScale: loadScale(),
  theme: loadTheme(),
  density: loadDensity(),
});

export const useWindows = create<WinState>((set, get) => ({
  ...initial(),
  openApp: (id, slug) => {
    const st = get();
    if (st.open.includes(id)) { get().focus(id); set({ launcherOpen: false }); return; }
    const zTop = st.zTop + 1;
    const pos = st.pos[id] ? st.pos : { ...st.pos, [id]: { x: 60 + st.open.length * 24, y: 40 } };
    set({ open: [...st.open, id], z: { ...st.z, [id]: zTop }, zTop, pos, launcherOpen: false });
  },
  focus: (id) => {
    const st = get();
    if (!st.open.includes(id)) return;
    // Focusing always restores a minimized window (the Waybar task button
    // calls focus() — that's the restore path).
    const minimized = st.minimized[id]
      ? { ...st.minimized, [id]: false }
      : st.minimized;
    if (st.z[id] === st.zTop) {
      if (minimized !== st.minimized) set({ minimized });
      return;
    }
    const zTop = st.zTop + 1;
    set({ z: { ...st.z, [id]: zTop }, zTop, minimized });
  },
  close: (id) => {
    const st = get();
    const z = { ...st.z };
    delete z[id];
    const minimized = { ...st.minimized };
    delete minimized[id];
    set({ open: st.open.filter((a) => a !== id), z, minimized });
  },
  minimize: (id) => {
    const st = get();
    if (!st.open.includes(id)) return;
    set({ minimized: { ...st.minimized, [id]: true } });
  },
  setPos: (id, x, y) => set({ pos: { ...get().pos, [id]: { x, y } } }),
  setSize: (id, w, h) => set({ size: { ...get().size, [id]: { w, h } } }),
  setDetailSlug: (slug) => set({ detailSlug: slug }),
  toggleLauncher: () => set({ launcherOpen: !get().launcherOpen, launcherQuery: "" }),
  toggleCommandBar: () => set({ commandBarOpen: !get().commandBarOpen }),
  setCommandBarOpen: (open) => set({ commandBarOpen: open }),
  togglePanel: () => set({ panelOpen: !get().panelOpen }),
  setDesktopScale: (scale) => {
    const s = clampScale(scale);
    try {
      localStorage.setItem(SCALE_KEY, String(s));
    } catch {
      /* ignore storage failures (private mode etc.) */
    }
    set({ desktopScale: s });
  },
  setTheme: (theme) => {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore storage failures */
    }
    applyPrefsToDom(theme, get().density);
    set({ theme });
  },
  setDensity: (density) => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {
      /* ignore storage failures */
    }
    applyPrefsToDom(get().theme, density);
    set({ density });
  },
  setLauncherQuery: (q) => set({ launcherQuery: q }),
  reset: () => {
    const st = initial();
    applyPrefsToDom(st.theme, st.density);
    set(st);
  },
}));

// Apply persisted theme/density on load — before this, nothing ever set
// data-theme/data-density and the theme.css overrides were dead selectors.
applyPrefsToDom(useWindows.getState().theme, useWindows.getState().density);

export function focusedApp(state: WinState): AppId | null {
  let best: AppId | null = null;
  let bestZ = -1;
  for (const id of state.open) {
    if (state.minimized[id]) continue;
    const z = state.z[id] ?? 0;
    if (z > bestZ) { bestZ = z; best = id; }
  }
  return best;
}

export function sizeOf(state: WinState, id: AppId): WH {
  return state.size[id] || { w: APPS[id].w, h: APPS[id].h };
}
