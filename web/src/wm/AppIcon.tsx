import React from "react";
import { APPS, ICON_FILE, type AppId } from "./apps";

// Eagerly import every generated app-tile PNG so Vite bundles them into
// dist/assets (they ship with the build). Keyed by icon name (filename stem).
const ICON_MODULES = import.meta.glob("../assets/icons/*.png", {
  eager: true,
}) as Record<string, { default: string }>;
const ICON_BY_NAME: Record<string, string> = {};
for (const [path, mod] of Object.entries(ICON_MODULES)) {
  const stem = path.split("/").pop()?.replace(/\.png$/, "");
  if (stem) ICON_BY_NAME[stem] = mod.default;
}

/**
 * Uniform app icon. Renders the generated PNG tile when one exists for the
 * app's ICON_FILE name; otherwise falls back to the unicode glyph centered on
 * the app's themed gradient (for the few apps without a generated tile).
 */
export function AppIcon({
  id,
  size,
  radius,
  filter,
  className,
}: {
  id: AppId;
  size: number;
  radius: number;
  filter?: string;
  className?: string;
}) {
  const a = APPS[id];
  const name = ICON_FILE[id];
  const url = name ? ICON_BY_NAME[name] : undefined;

  if (url) {
    return (
      <img
        className={className}
        src={url}
        alt={a.label}
        draggable={false}
        style={{
          width: size,
          height: size,
          borderRadius: radius,
          objectFit: "cover",
          filter,
        }}
      />
    );
  }

  // Fallback: glyph on themed gradient.
  return (
    <div
      className={className}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        background: a.grad,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        filter,
        boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.12)",
      }}
    >
      <span
        aria-hidden
        style={{
          fontSize: Math.round(size * 0.5),
          lineHeight: 1,
          color: "rgba(255,255,255,0.96)",
          textShadow: "0 1px 2px rgba(0,0,0,0.45)",
        }}
      >
        {a.glyph}
      </span>
    </div>
  );
}
