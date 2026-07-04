import React from "react";
import { APPS, ICON_FILE, type AppId } from "./apps";

// Import ONLY the icons the 8 registered apps actually use, so Vite bundles
// those 8 PNGs and not the ~40 leftover tiles that used to ship via an eager
// glob of the whole icons/ directory (F3 — dead bundle weight).
import browseIcon from "../assets/icons/browse.png";
import wikiIcon from "../assets/icons/wiki.png";
import vaultIcon from "../assets/icons/vault.png";
import credsIcon from "../assets/icons/creds.png";
import reviewIcon from "../assets/icons/review.png";
import pipelineIcon from "../assets/icons/pipeline.png";
import commandIcon from "../assets/icons/command.png";
import settingsIcon from "../assets/icons/settings.png";

const ICON_BY_NAME: Record<string, string> = {
  browse: browseIcon,
  wiki: wikiIcon,
  vault: vaultIcon,
  creds: credsIcon,
  review: reviewIcon,
  pipeline: pipelineIcon,
  command: commandIcon,
  settings: settingsIcon,
};

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
