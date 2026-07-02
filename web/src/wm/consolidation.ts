// web/src/wm/consolidation.ts
// Agent Vault: all 7 apps are single-screen (no tabbed groups). Window.tsx uses
// isTabbedGroup() to decide whether to render TabbedApp vs ScreenRouter.

import type { AppId } from "./apps";

export interface TabDef {
  id: AppId;
  label: string;
}

// No tabbed groups in the vault — each AppId maps directly to one screen.
export const TABBED_GROUPS: Partial<Record<AppId, TabDef[]>> = {};

/** Returns true iff the AppId is a tabbed group (never, for the vault). */
export function isTabbedGroup(_id: AppId): boolean {
  return false;
}
