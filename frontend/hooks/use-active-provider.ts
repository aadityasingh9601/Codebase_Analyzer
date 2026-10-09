"use client";

import { useSyncExternalStore } from "react";
import {
  PROVIDER_CHANGE_EVENT,
  readActiveProviderRaw,
  type ActiveProvider,
} from "@/lib/api-key-store";

/** Same caching trick as use-selected-repo, for the sessionStorage store. */
let cachedRaw: string | null = null;
let cachedKey: ActiveProvider | null = null;

function getSnapshot(): ActiveProvider | null {
  const raw = readActiveProviderRaw();

  if (raw === cachedRaw) {
    return cachedKey;
  }

  cachedRaw = raw;
  try {
    cachedKey = raw ? (JSON.parse(raw) as ActiveProvider) : null;
  } catch {
    cachedKey = null;
  }
  return cachedKey;
}

function subscribe(callback: () => void) {
  window.addEventListener(PROVIDER_CHANGE_EVENT, callback);
  return () => {
    window.removeEventListener(PROVIDER_CHANGE_EVENT, callback);
  };
}

function getServerSnapshot(): ActiveProvider | null {
  return null;
}

export function useActiveProvider(): ActiveProvider | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}