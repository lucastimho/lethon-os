"use client";

import * as React from "react";

/**
 * Global keyboard shortcuts. Skips when focus is in an input/textarea/
 * contenteditable so command-bar typing isn't intercepted.
 */
export type Shortcut = {
  /** Single character (lowercase) or symbolic key like "Escape", "/", "?" */
  key: string;
  /** Optional modifier requirement. */
  meta?: boolean;
  shift?: boolean;
  handler: (e: KeyboardEvent) => void;
  /** Force-fire even when focus is in an editable element (e.g. Esc). */
  alwaysFire?: boolean;
};

const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (EDITABLE_TAGS.has(el.tagName)) return true;
  return el.isContentEditable;
}

export function useKeyboardShortcuts(shortcuts: Shortcut[]): void {
  // Stash the latest shortcut list in a ref so handlers don't need to
  // re-bind on every render.
  const ref = React.useRef(shortcuts);
  React.useEffect(() => {
    ref.current = shortcuts;
  }, [shortcuts]);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const editable = isEditableTarget(e.target);
      for (const s of ref.current) {
        if (e.key.toLowerCase() !== s.key.toLowerCase()) continue;
        if (s.meta !== undefined && e.metaKey !== s.meta) continue;
        if (s.shift !== undefined && e.shiftKey !== s.shift) continue;
        if (editable && !s.alwaysFire) continue;
        s.handler(e);
        return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
