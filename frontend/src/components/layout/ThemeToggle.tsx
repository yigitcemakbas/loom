import { useEffect, useState } from "react";

/** Switch between the light and dark palettes.
 *
 *  The attribute is the single source of truth and it is already set before
 *  React runs, by the inline script in index.html — resolving the system
 *  preference after hydration shows every dark-mode user a flash of the light
 *  palette on every load. This component only reads that attribute and writes
 *  to it, so there is never a second opinion about what the theme is.
 *
 *  An explicit choice is remembered and outranks the system preference from
 *  then on, which is the behaviour someone expects after deliberately flipping
 *  a switch. Until they do, the system decides. */

type Theme = "light" | "dark";

const KEY = "loom.theme";

function current(): Theme {
  const set = document.documentElement.dataset.theme;
  return set === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(current);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // Private browsing and blocked site data both throw. The theme still
      // applies for this session; only the memory of it is lost.
    }
  }, [theme]);

  // Follow the system while no explicit choice has been stored, so changing the
  // OS appearance mid-session is reflected rather than ignored.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(KEY);
    } catch {
      /* see above */
    }
    if (stored === "dark" || stored === "light") return;

    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = (e: MediaQueryListEvent) => setTheme(e.matches ? "dark" : "light");
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);

  const next = theme === "dark" ? "light" : "dark";

  return (
    <button
      className="theme-toggle"
      onClick={() => setTheme(next)}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
    >
      {/* Two glyphs rather than one that changes meaning: the control shows
          what it will do, not what the state currently is. */}
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}
