"use client";

import { useSyncExternalStore } from "react";
import {
  API_KEY_CHANGE_EVENT,
  readActiveApiKeyRaw,
  type ActiveApiKey,
} from "@/lib/api-key-store";

/** Same caching trick as use-selected-repo, for the sessionStorage store. */
let cachedRaw: string | null = null;
let cachedKey: ActiveApiKey | null = null;

function getSnapshot(): ActiveApiKey | null {
  const raw = readActiveApiKeyRaw();

  if (raw === cachedRaw) {
    return cachedKey;
  }

  cachedRaw = raw;
  try {
    cachedKey = raw ? (JSON.parse(raw) as ActiveApiKey) : null;
  } catch {
    cachedKey = null;
  }
  return cachedKey;
}

function subscribe(callback: () => void) {
  window.addEventListener(API_KEY_CHANGE_EVENT, callback);
  return () => {
    window.removeEventListener(API_KEY_CHANGE_EVENT, callback);
  };
}

function getServerSnapshot(): ActiveApiKey | null {
  return null;
}

export function useActiveApiKey(): ActiveApiKey | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}