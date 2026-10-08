/**
 * Active-session API key shared between /keys and /chat.
 *
 * The plaintext key is only ever kept in this tab's sessionStorage, so it is
 * cleared when the tab (or browser) closes and never survives a session. It is
 * read per request by the chat and sent over HTTPS to the backend, which uses
 * it for that one call and discards it.
 *
 * Tab-scoped on purpose: the plaintext key must not leak to other tabs.
 */

export const PROVIDERS = ["openrouter", "openai", "anthropic"] as const;
export type ProviderId = (typeof PROVIDERS)[number];

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
};

const ACTIVE_KEY_STORAGE_KEY = "repochat-active-api-key";

/** Fired on `window` whenever the active key is set or cleared. */
export const API_KEY_CHANGE_EVENT = "repochat:api-key-changed";

export type ActiveApiKey = {
  provider: ProviderId;
  apiKey: string;
  keyHint: string;
};

export function isProviderId(value: string): value is ProviderId {
  return (PROVIDERS as readonly string[]).includes(value);
}

/** The raw sessionStorage string, or null. Uncached — callers may memoise. */
export function readActiveApiKeyRaw(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(ACTIVE_KEY_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getActiveApiKey(): ActiveApiKey | null {
  const raw = readActiveApiKeyRaw();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ActiveApiKey;
  } catch {
    return null;
  }
}

export function setActiveApiKey(key: ActiveApiKey): void {
  try {
    sessionStorage.setItem(ACTIVE_KEY_STORAGE_KEY, JSON.stringify(key));
    window.dispatchEvent(new Event(API_KEY_CHANGE_EVENT));
  } catch {
    // Non-fatal: chat just behaves as if no key is unlocked.
  }
}

export function clearActiveApiKey(): void {
  try {
    sessionStorage.removeItem(ACTIVE_KEY_STORAGE_KEY);
    window.dispatchEvent(new Event(API_KEY_CHANGE_EVENT));
  } catch {
    // Ignore.
  }
}