"use client";

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "repochat-theme";

export type Theme = "light" | "dark";

/**
 * The theme lives on `<html data-theme>`, written by the pre-paint script in
 * layout.tsx. It is an external store rather than React state, so we read it
 * with useSyncExternalStore — that also gives a stable server snapshot, which
 * avoids the hydration mismatch you would get from useState + useEffect.
 */
function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

function getServerSnapshot(): Theme {
  return "light";
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setTheme = useCallback((next: Theme) => {
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode or storage disabled — the in-memory toggle still works.
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(getSnapshot() === "dark" ? "light" : "dark");
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}
