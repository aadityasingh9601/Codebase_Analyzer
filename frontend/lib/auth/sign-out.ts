"use client";

import { clearRedirect } from "@/lib/auth/redirect";
import { clearSelectedRepo } from "@/lib/repo";

/**
 * Cookies written by the first version of the OAuth callback route. That
 * exchange never succeeded, so these are almost certainly empty — but a
 * browser that once received them would keep sending them, and they are token
 * shaped, so they get expired explicitly rather than left to chance.
 */
const LEGACY_AUTH_COOKIES = ["sb-access-token", "sb-refresh-token", "repochat-session"];

/** Matches `sb-<project-ref>-auth-token` and its `.0`/`.1` cookie chunks. */
const LOCALSTORAGE_AUTH_KEY = /^sb-[a-z0-9]+-auth-token/;

function expireCookie(name: string): void {
  document.cookie = `${name}=; path=/; max-age=0; samesite=lax`;
}

/**
 * Signs the user out and removes every trace of the session.
 *
 * Three separate things have to go, and missing any one of them leaves a way
 * back in:
 *
 *  1. The server-side session. `signOut()` revokes the refresh token with
 *     Supabase, so a copy of it that leaked elsewhere stops working.
 *  2. The session cookies. `@supabase/ssr` clears the chunks it owns; the
 *     legacy ones need expiring by hand.
 *  3. Auth state left in localStorage. Sessions used to be stored there, so
 *     anyone who signed in under the old scheme still has a token on disk that
 *     nothing else will remove.
 *
 * Then a full document load, not a client-side route change: it guarantees the
 * gate re-runs and no cached render of a protected page stays on screen.
 */
export async function signOutAndCleanup(): Promise<void> {
  try {
    const { isSupabaseConfigured } = await import("@/lib/supabase/env");

    if (isSupabaseConfigured()) {
      const { getSupabase } = await import("@/lib/supabase/client");
      await getSupabase().auth.signOut();
    }
  } catch {
    // Offline, already signed out, or no project configured. The local cleanup
    // below still has to run — that is the whole point of this function.
  }

  for (const name of LEGACY_AUTH_COOKIES) {
    expireCookie(name);
  }

  try {
    const stale: string[] = [];

    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (key && LOCALSTORAGE_AUTH_KEY.test(key)) stale.push(key);
    }

    for (const key of stale) {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Storage unavailable (private mode); cookies are already gone.
  }

  clearRedirect();
  // The selected repo belongs to the account that just left.
  clearSelectedRepo();
}

/** `signOutAndCleanup`, then leave for the login page. */
export async function signOutAndRedirect(): Promise<void> {
  await signOutAndCleanup();
  window.location.replace("/auth");
}
