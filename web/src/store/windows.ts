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
  detailSlug: string;
  launcherOpen: boolean;
  commandBarOpen: boolean;
  panelOpen: boolean;
  launcherQuery: string;
  desktopScale: number;
  theme: "dark" | "light";
  density: "comfortable" | "compact";
  locale: string;
  setDesktopScale: (scale: number) => void;
  setTheme: (theme: "dark" | "light") => void;
  setDensity: (density: "comfortable" | "compact") => void;
  setLocale: (locale: string) => void;
  openApp: (id: AppId, slug?: string) => void;
  focus: (id: AppId) => void;
  close: (id: AppId) => void;
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
const LOCALE_KEY = "vault.locale";
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

function loadLocale(): string {
  try {
    const raw = localStorage.getItem(LOCALE_KEY);
    return raw && raw.trim() ? raw : "en";
  } catch {
    return "en";
  }
}

const initial = () => ({
  open: ["vault"] as AppId[],
  z: { vault: 1 } as Partial<Record<AppId, number>>,
  zTop: 1,
  pos: { ...DEFAULT_POS },
  size: {} as Partial<Record<AppId, WH>>,
  detailSlug: "",
  launcherOpen: false,
  commandBarOpen: false,
  panelOpen: false,
  launcherQuery: "",
  desktopScale: loadScale(),
  theme: loadTheme(),
  density: loadDensity(),
  locale: loadLocale(),
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
    if (st.z[id] === st.zTop) return;
    const zTop = st.zTop + 1;
    set({ z: { ...st.z, [id]: zTop }, zTop });
  },
  close: (id) => {
    const st = get();
    const z = { ...st.z };
    delete z[id];
    set({ open: st.open.filter((a) => a !== id), z });
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
    set({ theme });
  },
  setDensity: (density) => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {
      /* ignore storage failures */
    }
    set({ density });
  },
  setLocale: (locale) => {
    try {
      localStorage.setItem(LOCALE_KEY, locale);
    } catch {
      /* ignore storage failures */
    }
    set({ locale });
  },
  setLauncherQuery: (q) => set({ launcherQuery: q }),
  reset: () => set(initial()),
}));

export function focusedApp(state: WinState): AppId | null {
  let best: AppId | null = null;
  let bestZ = -1;
  for (const id of state.open) {
    const z = state.z[id] ?? 0;
    if (z > bestZ) { bestZ = z; best = id; }
  }
  return best;
}

export function sizeOf(state: WinState, id: AppId): WH {
  return state.size[id] || { w: APPS[id].w, h: APPS[id].h };
}
