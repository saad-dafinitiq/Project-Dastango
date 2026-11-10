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

/** Whether the palette in use is the dark one: the chosen theme, or the device's when none is chosen. The WebGL and
 *  canvas components cannot read CSS variables, so they are given concrete colours from this. */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    const root = document.documentElement;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const read = () => setDark(root.dataset.theme ? root.dataset.theme === "dark" : query.matches);
    read();
    const watch = new MutationObserver(read);
    watch.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    query.addEventListener("change", read);
    return () => {
      watch.disconnect();
      query.removeEventListener("change", read);
    };
  }, []);
  return dark;
}
