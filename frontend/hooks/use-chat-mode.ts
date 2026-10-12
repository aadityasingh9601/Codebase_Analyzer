"use client";

import { useSyncExternalStore } from "react";
import { getChatMode, type ChatMode } from "@/lib/chat-mode-store";

function getSnapshot(): ChatMode {
  return getChatMode();
}

function subscribe(callback: () => void) {
  window.addEventListener("repochat:chat-mode-changed", callback);
  return () => window.removeEventListener("repochat:chat-mode-changed", callback);
}

function getServerSnapshot(): ChatMode {
  return "free";
}

export function useChatMode(): ChatMode {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
