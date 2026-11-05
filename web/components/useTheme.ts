"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "dastango-theme";

/** The creator's theme choice. "system" follows the device (globals.css reads the
 *  system setting when no `data-theme` is set); a choice is kept in this browser. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>("system");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === "light" || saved === "dark") setChoice(saved);
    } catch {
      /* private window: the system setting stands */
    }
  }, []);

  const choose = useCallback((next: ThemeChoice) => {
    setChoice(next);
    const root = document.documentElement;
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      /* not kept, but it still applies to this visit */
    }
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
  }, []);

  return { choice, choose };
}
