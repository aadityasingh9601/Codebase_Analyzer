/**
 * Active provider shared between /keys and /chat.
 *
 * The plaintext API key no longer lives in the browser at all — Phase 2 of
 * BYOK: the frontend only remembers WHICH saved provider should answer the
 * user's questions, and sends just that `provider` id per request. The backend
 * re-derives the user's encryption key, decrypts the stored blob, uses it for
 * that one answer and discards it.
 *
 * Kept in sessionStorage so the choice is cleared when the tab closes, and
 * scoped to this tab on purpose.
 */

export const PROVIDERS = ["openrouter", "openai", "anthropic"] as const;
export type ProviderId = (typeof PROVIDERS)[number];

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
};

const ACTIVE_PROVIDER_STORAGE_KEY = "repochat-active-provider";

/** Fired on `window` whenever the active provider is set or cleared. */
export const PROVIDER_CHANGE_EVENT = "repochat:provider-changed";

export type ActiveProvider = {
  provider: ProviderId;
  keyHint: string;
};

export function isProviderId(value: string): value is ProviderId {
  return (PROVIDERS as readonly string[]).includes(value);
}

/** The raw sessionStorage string, or null. Uncached — callers may memoise. */
export function readActiveProviderRaw(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(ACTIVE_PROVIDER_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getActiveProvider(): ActiveProvider | null {
  const raw = readActiveProviderRaw();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ActiveProvider;
  } catch {
    return null;
  }
}

export function setActiveProvider(provider: ActiveProvider): void {
  try {
    sessionStorage.setItem(ACTIVE_PROVIDER_STORAGE_KEY, JSON.stringify(provider));
    window.dispatchEvent(new Event(PROVIDER_CHANGE_EVENT));
  } catch {
    // Non-fatal: chat just behaves as if no provider is selected.
  }
}

export function clearActiveProvider(): void {
  try {
    sessionStorage.removeItem(ACTIVE_PROVIDER_STORAGE_KEY);
    window.dispatchEvent(new Event(PROVIDER_CHANGE_EVENT));
  } catch {
    // Ignore.
  }
}