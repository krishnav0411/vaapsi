/**
 * Theme toggle: flips the .dark class on <html> (the index.css token
 * flip does all the color work — no component owns a color) and
 * persists the choice in localStorage under "vaapsi-theme". Light is
 * the default, both for a missing key and for a thrown/absent storage.
 * The 160ms color transition is armed only when the user has not asked
 * for reduced motion (the CSS media query in index.css is the second
 * lock), and is stripped again right after the flip.
 */

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "light" | "dark";

const STORAGE_KEY = "vaapsi-theme";
const TRANSITION_MS = 200;

/**
 * The command palette toggles the theme through this window event instead
 * of owning a second theme state — the toggle component remains the one
 * source of truth (class flip + localStorage write live only here).
 */
export const THEME_TOGGLE_EVENT = "vaapsi:toggle-theme";

function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    // Storage unavailable (privacy mode) — light default, session-only choice.
    return "light";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const dark = theme === "dark";

  useEffect(() => {
    function onExternalToggle() {
      setTheme((current) => (current === "dark" ? "light" : "dark"));
    }
    window.addEventListener(THEME_TOGGLE_EVENT, onExternalToggle);
    return () => window.removeEventListener(THEME_TOGGLE_EVENT, onExternalToggle);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const allowMotion = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (allowMotion) {
      root.classList.add("theme-transition");
      window.setTimeout(() => root.classList.remove("theme-transition"), TRANSITION_MS);
    }
    root.classList.toggle("dark", dark);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Persisting is best-effort; the toggle itself still works.
    }
  }, [dark, theme]);

  return (
    <button
      type="button"
      aria-pressed={dark}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="inline-flex h-control-sm w-control-sm items-center justify-center rounded-button border border-border-normal text-text-subtle hover:border-border-hover hover:bg-row-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {dark ? (
        <Sun className="h-16 w-16" aria-hidden />
      ) : (
        <Moon className="h-16 w-16" aria-hidden />
      )}
    </button>
  );
}
