"use client";

import { useSyncExternalStore } from "react";
import { REPO_CHANGE_EVENT, readRepoRaw, type SelectedRepo } from "@/lib/repo";

/**
 * Reads the stored repo as an external store. localStorage lives outside
 * React, so this avoids both the hydration mismatch of useState + useEffect and
 * the extra render pass the lint rule rightly complains about.
 *
 * `useSyncExternalStore` requires getSnapshot to return a *cached* value: a
 * fresh object each call makes React think the store changed and it loops
 * forever. So we memoise the parse against the raw localStorage string and
 * only rebuild when that string actually differs.
 */
let cachedRaw: string | null = null;
let cachedRepo: SelectedRepo | null = null;

function getSnapshot(): SelectedRepo | null {
  const raw = readRepoRaw();

  if (raw === cachedRaw) {
    return cachedRepo;
  }

  cachedRaw = raw;
  try {
    cachedRepo = raw ? (JSON.parse(raw) as SelectedRepo) : null;
  } catch {
    cachedRepo = null;
  }
  return cachedRepo;
}

function subscribe(callback: () => void) {
  window.addEventListener(REPO_CHANGE_EVENT, callback);
  // Fires when another tab changes the selection.
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(REPO_CHANGE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

function getServerSnapshot(): SelectedRepo | null {
  return null;
}

export function useSelectedRepo(): SelectedRepo | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
