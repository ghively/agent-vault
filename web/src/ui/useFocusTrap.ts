import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),' +
  'select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Trap Tab focus inside a modal container and restore focus to the previously
 * focused element on close (F5). The component keeps ownership of its own
 * initial focus; this hook only cycles Tab/Shift+Tab within `ref` and prevents
 * focus from escaping to the window behind the overlay.
 */
export function useFocusTrap(ref: RefObject<HTMLElement>, active = true): void {
  useEffect(() => {
    if (!active) return;
    const node = ref.current;
    if (!node) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      // Note: we intentionally don't filter on offsetParent (always null in
      // jsdom, and modal focusables are visible by construction).
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const activeEl = document.activeElement;
      if (e.shiftKey && activeEl === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault();
        first.focus();
      } else if (activeEl && !node.contains(activeEl)) {
        // Focus somehow escaped (e.g. programmatic) — pull it back in.
        e.preventDefault();
        first.focus();
      }
    };

    node.addEventListener("keydown", onKey);
    return () => {
      node.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [ref, active]);
}
