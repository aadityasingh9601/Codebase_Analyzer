/**
 * Chat mode: "free" (server's OpenRouter key, metered) or "byok" (user's stored
 * provider key, rotates across their keys).
 */
import { PROVIDERS, type ProviderId } from "@/lib/api-key-store";

export type ChatMode = "free" | "byok";

export const CHAT_MODE_STORAGE_KEY = "repochat-chat-mode";

export function isChatMode(value: string | null): value is ChatMode {
  return value === "free" || value === "byok";
}

export function getChatMode(): ChatMode {
  if (typeof window === "undefined") return "free";
  try {
    const raw = sessionStorage.getItem(CHAT_MODE_STORAGE_KEY);
    if (isChatMode(raw)) return raw;
  } catch {}
  return "free";
}

export function setChatMode(mode: ChatMode): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(CHAT_MODE_STORAGE_KEY, mode);
    window.dispatchEvent(new Event("repochat:chat-mode-changed"));
  } catch {}
}

export function defaultByokProvider(): ProviderId {
  return PROVIDERS[0];
}
