"use client";

import { useEffect, useSyncExternalStore } from "react";
import type { User } from "@supabase/supabase-js";
import { isSupabaseConfigured } from "@/lib/supabase/env";

/**
 * Where the app is with respect to authentication.
 *
 * `unconfigured` exists because this UI is reviewable before any Supabase
 * project exists. In that state auth cannot be enforced, so the guard lets
 * pages through in demo mode instead of locking everyone out of the app.
 */
export type SessionStatus =
  | "checking"
  | "authenticated"
  | "anonymous"
  | "unconfigured";

export type SessionSnapshot = {
  status: SessionStatus;
  user: User | null;
};

const listeners = new Set<() => void>();

let snapshot: SessionSnapshot = {
  status: isSupabaseConfigured() ? "checking" : "unconfigured",
  user: null,
};

function setSnapshot(next: SessionSnapshot): void {
  // `onAuthStateChange` fires for token refreshes too, which hand back a new
  // `user` object for the same account. Comparing by id keeps those from
  // re-rendering every subscriber.
  if (next.status === snapshot.status && next.user?.id === snapshot.user?.id) return;

  snapshot = next;
  for (const listener of listeners) listener();
}

let started = false;

/**
 * Kicks off the one-time Supabase subscription. Idempotent, and safe to call
 * from an effect: it only touches module state and listeners, never React state.
 */
function start(): void {
  if (started) return;
  started = true;

  if (!isSupabaseConfigured()) {
    setSnapshot({ status: "unconfigured", user: null });
    return;
  }

  void (async () => {
    const { getSupabase } = await import("@/lib/supabase/client");
    const supabase = getSupabase();

    // Keeps the navbar in sync on sign-in, sign-out and token refresh.
    supabase.auth.onAuthStateChange((_event, session) => {
      setSnapshot({
        status: session ? "authenticated" : "anonymous",
        user: session?.user ?? null,
      });
    });

    // `getSession()` renews an expired access token from the stored refresh
    // token before answering, so this resolves to the real state rather than
    // treating a stale-but-renewable session as signed out.
    const { data } = await supabase.auth.getSession();

    setSnapshot({
      status: data.session ? "authenticated" : "anonymous",
      user: data.session?.user ?? null,
    });
  })();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): SessionSnapshot {
  return snapshot;
}

function getServerSnapshot(): SessionSnapshot {
  // Always "checking" on the server: the client resolves the session before
  // rendering anything protected, so no signed-out HTML is ever prerendered.
  return { status: isSupabaseConfigured() ? "checking" : "unconfigured", user: null };
}

export function useSession(): SessionSnapshot {
  useEffect(start, []);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
