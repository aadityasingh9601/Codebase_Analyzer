/**
 * Supabase configuration.
 *
 * Intentionally free of any Supabase imports so the same values can be read
 * from the proxy, from Server Components and from the browser bundle. Each of
 * those needs its own client (see `client.ts`, `server.ts`, `proxy.ts`), and a
 * single module importing all three would drag server-only code into the client.
 *
 * The publishable key is designed to be public: RLS, not the key, is what
 * protects data. Never put a service-role or secret key here — every value in
 * this file is readable by anyone who loads the app.
 */

export function getSupabaseUrl(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
}

export function getSupabaseKey(): string {
  return (
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
    ""
  );
}

/**
 * True once both required env vars are present.
 *
 * Auth is enforced only when this is true. With no project configured nobody
 * *can* sign in, so hard-blocking the app would just make it unreachable —
 * the pages fall back to the demo-mode behaviour instead.
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(getSupabaseUrl() && getSupabaseKey());
}

/**
 * Cookie name Supabase stores the session under: `sb-<project-ref>-auth-token`.
 * Derived from the project URL rather than hardcoded, since the ref differs per
 * environment. Values longer than a cookie's ~4KB budget are split into
 * `sb-<project-ref>-auth-token.0`, `.1`, … so callers must match on prefix.
 */
export function getSupabaseStorageKey(): string {
  let projectRef = "";

  try {
    projectRef = new URL(getSupabaseUrl()).hostname.split(".")[0] ?? "";
  } catch {
    projectRef = "";
  }

  return `sb-${projectRef}-auth-token`;
}

/**
 * True when any chunk of the session cookie is present on the request.
 *
 * Presence alone proves nothing — the token still has to be verified — but it
 * does distinguish "never signed in" from "signed in, access token expired",
 * which need different handling. See the proxy for why that matters.
 */
export function hasSessionCookie(cookies: { name: string }[]): boolean {
  const storageKey = getSupabaseStorageKey();
  return cookies.some((cookie) => cookie.name.startsWith(storageKey));
}

/**
 * Shared by every client so the browser, server and proxy all agree on the
 * cookie's name, path and scope. A mismatch here produces intermittent,
 * very-hard-to-debug logouts, so it is defined in exactly one place.
 */
export const SUPABASE_COOKIE_OPTIONS = {
  path: "/",
  sameSite: "lax",
  httpOnly: false,
  secure: process.env.NODE_ENV === "production",
} as const;
