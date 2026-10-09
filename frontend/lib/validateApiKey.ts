import { PROVIDER_LABELS, type ProviderId } from "@/lib/api-key-store";

/**
 * Frontend-only sanity check for provider API keys. Lengths here are the bare
 * minimum — the definitive validator is the authenticated provider call the
 * backend makes at save time. This just catches obvious typos and paste errors
 * before they leave the machine.
 */

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

export const KEY_PATTERNS: Record<
  ProviderId,
  { prefixes: string[]; minLength: number; example: string }
> = {
  openai: {
    prefixes: ["sk-", "sk-proj-"],
    minLength: 40,
    example: "sk-…",
  },
  anthropic: {
    prefixes: ["sk-ant-"],
    minLength: 90,
    example: "sk-ant-…",
  },
  openrouter: {
    prefixes: ["sk-or-v1-"],
    minLength: 60,
    example: "sk-or-v1-…",
  },
};

export function validateApiKey(provider: ProviderId, key: string): ValidationResult {
  const trimmed = key.trim();

  if (!trimmed) {
    return { valid: false, error: "API key cannot be empty" };
  }

  const pattern = KEY_PATTERNS[provider];
  if (!pattern) {
    return { valid: false, error: "Unknown provider" };
  }

  const hasValidPrefix = pattern.prefixes.some((p) => trimmed.startsWith(p));
  if (!hasValidPrefix) {
    return {
      valid: false,
      error: `${PROVIDER_LABELS[provider]} keys start with ${pattern.prefixes.join(" or ")}`,
    };
  }

  if (trimmed.length < pattern.minLength) {
    return {
      valid: false,
      error: `Key too short. ${PROVIDER_LABELS[provider]} keys are at least ${pattern.minLength} characters`,
    };
  }

  if (!/^[a-zA-Z0-9\-_]+$/.test(trimmed)) {
    return { valid: false, error: "Key contains invalid characters" };
  }

  return { valid: true };
}