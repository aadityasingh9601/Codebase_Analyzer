/**
 * Provider catalog shared by /keys, /chat and the API-key server action.
 *
 * The plaintext API key never lives in the browser at all — Phase 2 of BYOK:
 * the frontend only sends WHICH provider should answer the user's questions,
 * and the backend re-derives the user's encryption key, decrypts the stored
 * blob, uses it for that one answer and discards it.
 */

export const PROVIDERS = ["openrouter", "openai", "anthropic"] as const;
export type ProviderId = (typeof PROVIDERS)[number];

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
};